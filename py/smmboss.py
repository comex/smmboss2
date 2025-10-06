#!/usr/bin/env python3

from guest_access import *
import socket, struct, sys, os, time, importlib
from threading import Lock
from binascii import hexlify
from functools import cache
from typing import Callable

def addrs_yaml_path():
    return os.path.join(os.path.dirname(__file__), '..', 'addrs.yaml')

@cache
def get_addrs_yaml():
    import yaml
    return yaml.safe_load(open(addrs_yaml_path()))

@cache
def _dotnote_addrs():
    return set(stuff.get('addrs', {}).get('dot_note') for stuff in get_addrs_yaml().values())

class MM:
    class Addr:
        # Just some syntax sugar for getting addresses:
        # allow mm.addr.foo rather than mm.yaml['addrs']['foo']
        def __init__(self, mm):
            self.mm = mm
        def __getattr__(self, name):
            return self.mm.slide(self.mm.yaml['addrs'][name])

    def __init__(self):
        raise 'do not call'

    def _init(self):
        self._world = None

    @classmethod
    def with_guest(cls, guest):
        self = cls.__new__(cls)
        self.guest = guest
        self._init()
        self.extract_image_info_etc()
        self.process_main_image_info()
        return self

    @classmethod
    def detached(cls, build_id):
        self = cls.__new__(cls, 42)
        self.guest = None
        self._init()
        self.main_image_info = {
            'build_id': build_id,
        }
        self.process_main_image_info()
        return self

    def extract_image_info_etc(self):
        image_infos = []
        main_image_info = None

        for info in self.guest.extract_image_info():
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
                for dotnote_addr in _dotnote_addrs():
                    if dotnote_addr is None or dotnote_addr >= info['image_size']:
                        continue
                    build_id_addr = dotnote_addr + 16
                    maybe_build_id = self.guest.try_read(info['image_start'] + build_id_addr, 16)
                    if len(maybe_build_id) != 16:
                        continue
                    padded = (bytes(maybe_build_id) + b'\0'*16).hex()
                    if padded in get_addrs_yaml():
                        info['build_id'] = padded
                        main_image_info = info
            image_infos.append(info)

        if main_image_info is None:
            raise Exception('unable to guess build ID')

        self.image_infos = image_infos
        self.main_image_info = main_image_info

    def process_main_image_info(self):
        self.yaml = get_addrs_yaml()[self.main_image_info['build_id']]
        self.version = self.yaml['version']
        if (slide := self.main_image_info.get('image_start')) is not None:
            self._slide = slide
        self.addr = self.Addr(self)

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

    @property
    def world(self):
        if self._world is None or self._world.stale_code:
            self._world = self.make_world()
        return self._world

    def stubbed_functions(self) -> dict[int, Callable[[], int]]:
        return {
            self.addr.cxa_guard_acquire: lambda: 0,
            self.addr.cxa_guard_release: lambda: 0,
        }
