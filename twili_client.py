from typing import Any, List
from dataclasses import dataclass
import struct
from io import BytesIO
import asyncio
import socket

MESSAGE_HEADER_FMT = '<IIIIQI'

class Twib:
    @clsmethod
    async def create(cls, path='/opt/twib/twibd.sock'):
        self = cls()
        self.reader, self.writer = await asyncio.open_unix_connection(path)
        self.last_tag = 0
        self.response_futures = {}
        asyncio.create_task(self.read_task())
        return self

    async def read_task(self):
        while True:
            h = await self.reader.read(struct.calcsize(MESSAGE_HEADER_FMT))
            (device_id, object_id, result_code, tag,
             payload_size, object_count) = struct.unpack(MESSAGE_HEADER_FMT, h)
            print('response device_id={device_id} object_id={object_id} result_code={result_code} tag={tag} payload_size={payload_size} object_count={object_count}')
            payload = await self.reader.read(payload_size)
            object_ids = []
            for i in range(object_count):
                object_ids.append(struct.unpack('<I', await self.reader.read(4))[0])
            unpacker = Unpacker(payload, object_ids, self, device_id)
            f = self.response_futures[tag]
            f.set_result((result_code, unpacker))
            del = self.response_futures[tag]

    def new_tag(self):
        self.last_tag += 1
        assert self.last_tag <= 0xffffffff
        return self.last_tag

    async def request(self, device_id, object_id, command_id, in_args, out_types):
        assert isinstance(in_args, tuple)
        assert isinstance(out_types, tuple)
        tag = self.new_tag()
        p = Packer()
        p.pack(in_args)
        payload, objects = p.finish()
        assert len(objects) == 0
        print('request device_id={self.device_id} object_id={self.object_id} result_code={self.result_code} tag={tag} payload_size={len(payload)}')
        message_header = struct.pack(MESSAGE_HEADER_FMT,
            self.device_id,
            self.object_id,
            self.command_id,
            tag,
            len(payload),
            0) # object_count
        self.writer.write(message_header + payload)
        f = asyncio.loop.create_future()
        self.twib.response_futures[tag] = f
        rc, unpacker = await f
        if rc:
            raise ResultError(rc)
        ret = unpacker.unpack(out_types)
        unpacker.finish()
        return ret

class Prim:
    def __init__(self, val):
        self.val = val
        self.desc = '<' + self.char
    def pack(self, packer):
        packer.ba.append(struct.pack(self.desc, self.val))
    @classmethod
    def unpack(cls, unpacker):
        val, = struct.unpack(self.desc, unpacker.read(struct.calcsize(self.desc)))
        return cls(val)

class uint64_t(Prim):
    char = 'Q'
class uint32_t(Prim):
    char = 'I'

class Packer:
    def __init__(self):
        self.ba = bytearray()
        self.objects = []
    def pack(x):
        if isinstance(x, tuple):
            for e in x:
                self.pack(e)
        elif isinstance(x, Prim):
            self.ba.extend(x.pack())
        elif isinstance(x, str):
            self.pack(x.encode('utf-8'))
        elif isinstance(x, bytes):
            self.pack(uint64_t(len(x)))
            self.ba.extend(x)
        elif isinstance(x, list):
            self.pack(uint64_t(len(x)))
            for v in x:
                self.pack(v)
        elif isinstance(x, RemoteObject):
            self.objects.append(x)
        else:
            raise ValueError(f"don't know how to pack {repr(x)}")
    def finish(self):
        return self.ba, self.objects

class Unpacker:
    def __init__(self, ba, object_ids, twib, device_id):
        self.fp = BytesIO(ba)
        self.object_ids = object_ids
        self.twib = twib
        self.device_id = device_id
    def read(self, n):
        x = self.fp.read(n)
        if len(x) < n:
            raise Exception('short read')
        return x
    def unpack(self, ty):
        if isinstance(ty, tuple):
            ret = ()
            for sty in ty:
                ret += (self.unpack(sty),)
            return ret
        elif issubclass(ty, Prim):
            return ty.unpack(self)
        elif ty is str:
            return self.unpack(bytes).decode('utf-8')
        elif ty is bytes:
            len = self.unpack(uint64_t)
            return self.read(len)
        elif isinstance(ty, typing.List):
            len = self.unpack(uint64_t)
            sty = ty.__args__[0]
            return [self.unpack(sty) for i in range(len)]
        elif ty is RemoteObject:
            object_id = self.object_ids.pop(0)
            return RemoteObject(self.twib, self.device_id, object_id)
    def finish(self):
        assert len(self.fp.read()) == 0
        assert len(self.object_ids) == 0

def fmt_result(rc):
    hi, lo = rc >> 9, rc & 0x1ff
    if lo == 0xef:
        return f'TWILI_RESULT({lo}'
    else:
        return '?'

class ResultError(Exception):
    def __init__(self, rc):
        self.rc = rc
    def __repr__(self):
        return f'ResultError(rc={self.rc:#x} / {fmt_result(self.rc)}'

class RemoteObject:
    def __init__(self, twib, device_id, object_id):
        self.twib = twib
        self.device_id = device_id
        self.object_id = object_id
    async def __aenter__(self):
        pass
    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
    def close(self):
        if self.object_id != 0:
            return self.request(0xffffffff)
        else:
            async def f(): pass
            return f()
    async def request(command_id, in_args, out_types):
        return await self.twib.request(self.device_id, self.object_id, command_id, in_args, out_types)

