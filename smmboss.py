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
        self.yaml = addrs_yaml[self.build_id]
        self.version = self.yaml['version']
    def slide(self, addr):
        return (addr + self._slide) & 0xffffffffffffffff
    def unslide(self, addr):
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

