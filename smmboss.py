#!/usr/bin/env python3

from guest_access import *
import socket, struct, sys, os, time, importlib
from threading import Lock
from binascii import hexlify
import yaml

addrs_yaml = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), 'addrs.yaml')))

class MM:
    class Addr:
        def __init__(self, mm):
            self.mm = mm
        def __getattr__(self, name):
            return self.mm.slide(self.mm.yaml['addrs'][name])
    def __init__(self, guest):
        self.guest = guest
        self.addr = self.Addr(self)
        dotnote_addrs = set(stuff.get('addrs', {}).get('dot_note') for stuff in addrs_yaml.values())

        image_infos = []
        main_image_info = None

        for info in guest.extract_image_info():
            text_addr = info['text_start']
            name = info.get('name')

            initial = self.guest.read(text_addr, 24)
            mod0_off, = struct.unpack('<I', initial[4:8])
            mod0_addr = text_addr + mod0_off
            if mod0_off == 8:
                # optimization
                mod0_data = initial[8:24]
            else:
                mod0_data = self.guest.read(mod0_addr, 16)
            mod0_magic, _, _, bss_end_rel = struct.unpack('<4sIII', mod0_data)
            assert mod0_magic == b'MOD0'
            bss_end_addr = mod0_addr + bss_end_rel

            info['image_start'] = text_addr
            info['image_end'] = bss_end_addr
            info['image_size'] = info['image_end'] - info['image_start']

            if main_image_info is None:
                for dotnote_addr in dotnote_addrs:
                    if dotnote_addr is None or dotnote_addr >= info['image_size']:
                        continue
                    build_id_addr = dotnote_addr + 16
                    maybe_build_id = self.guest.try_read(info['image_start'] + build_id_addr, 16)
                    if len(maybe_build_id) != 16:
                        continue
                    padded = (bytes(maybe_build_id) + b'\0'*16).hex()
                    if padded in addrs_yaml:
                        info['build_id'] = padded
                        main_image_info = info
            image_infos.append(info)

        if main_image_info is None:
            raise Exception('unable to guess build ID')

        self.image_infos = image_infos
        self.main_image_info = main_image_info

        self.yaml = addrs_yaml[main_image_info['build_id']]
        self.version = self.yaml['version']
        self._slide = main_image_info['image_start']

    def slide(self, addr):
        if addr == 0:
            return 0
        return (addr + self._slide) & 0xffffffffffffffff

    def unslide(self, addr):
        if addr == 0:
            return 0
        return (addr - self._slide) & 0xffffffffffffffff

    def unslide_ex(self, addr):
        if addr != 0:
            for ii in self.image_infos:
                if ii['image_start'] <= addr < ii['image_end']:
                    return (ii, addr - ii['image_start'])
        return (None, addr)

    @property
    def _gslide(self):
        return (self._slide - 0x7100000000) & 0xffffffffffffffff
    def gslide(self, addr):
        return (addr + self._gslide) & 0xffffffffffffffff
    def gunslide(self, addr):
        return (addr - self._gslide) & 0xffffffffffffffff

    def make_world(self):
        world = make_guest_access_world(self.guest)
        world.mm = self
        world._import('smmboss_world.py')
        return world
    @functools.cached_property
    def world(self):
        return self.make_world()
