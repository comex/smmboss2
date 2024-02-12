#!/usr/bin/env python3

from guest_access import *
import socket, struct, sys, os, time, importlib
from threading import Lock
from binascii import hexlify
import yaml

addrs_yaml = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), 'addrs.yaml')))

class MMGuest(Guest):
    class Addr:
        def __init__(self, guest):
            self.guest = guest
        def __getattr__(self, name):
            return self.guest.slide(self.guest.yaml['addrs'][name])
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.addr = self.Addr(self)
        if self.build_id is None:
            for build_id, yaml in list(addrs_yaml.items())[::-1]:
                build_id_raw = bytes.fromhex(build_id)
                try:
                    build_id_addr = yaml['addrs']['dot_note'] + 16
                except (KeyError, TypeError):
                    continue
                try:
                    maybe_build_id = self.read(self.slide(build_id_addr), 16)
                except: # TODO: use a proper class for read errors
                    continue
                if bytes(maybe_build_id) == build_id_raw[:16]:
                    self.build_id = build_id
                    break
            else:
                raise Exception('unable to guess build ID')
        self.yaml = addrs_yaml[self.build_id]
        self.version = self.yaml['version']
    def slide(self, addr):
        if addr == 0:
            return 0
        return (addr + self._slide) & 0xffffffffffffffff
    def unslide(self, addr):
        if addr == 0:
            return 0
        return (addr - self._slide) & 0xffffffffffffffff
    @property
    def _gslide(self):
        return (self._slide - 0x7100000000) & 0xffffffffffffffff
    def gslide(self, addr):
        return (addr + self._gslide) & 0xffffffffffffffff
    def gunslide(self, addr):
        return (addr - self._gslide) & 0xffffffffffffffff
    def make_world(self):
        world = super().make_world()
        world._import('smmboss_world.py')
        return world
    @functools.cached_property
    def world(self):
        return self.make_world()

