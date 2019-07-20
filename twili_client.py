import struct
from io import BytesIO
import asyncio
import socket
import msgpack
import binascii

class Prim:
    def __init__(self, char):
        self.desc = '<' + char
        self.size = struct.calcsize(self.desc)
    def pack(self, val, packer):
        packer.write(struct.pack(self.desc, val))
    def unpack(self, unpacker):
        return struct.unpack(self.desc, unpacker.read(self.size))[0]

uint64_t = Prim('Q')
uint32_t = Prim('I')

class MsgPack:
    @staticmethod
    def pack(val, packer):
        packer.pack(bytes, msgpack.packb(val, use_bin_type=True))
    @staticmethod
    def unpack(unpacker):
        return msgpack.unpackb(unpacker.unpack(bytes))

class List:
    def __init__(self, sty):
        self.sty = sty
    def pack(self, val, packer):
        packer.pack(uint64_t, len(val))
        for sval in val:
            packer.pack(self.sty, sval)
    def unpack(self, unpacker):
        count = unpacker.unpack(uint64_t)
        return [unpacker.unpack(self.sty) for i in range(count)]

def get_packer(ty):
    if hasattr(ty, 'pack'):
        pack = ty.pack
        unpack = ty.unpack
    elif isinstance(ty, tuple):
        def pack(val, packer):
            assert len(val) == len(ty)
            for sty, sval in zip(ty, val):
                packer.pack(sty, sval)
        def unpack(unpacker):
            ret = ()
            for sty in ty:
                ret += (unpacker.unpack(sty),)
            return ret
    elif ty is str:
        def pack(val, packer):
            packer.pack(bytes, val.encode('utf-8'))
        def unpack(unpacker):
            return unpacker.unpack(bytes).decode('utf-8')
    elif ty is bytes:
        def pack(val, packer):
            packer.pack(uint64_t, len(val))
            packer.write(val)
        def unpack(unpacker):
            count = unpacker.unpack(uint64_t)
            return unpacker.read(count)
    else:
        raise TypeError(f'get_packer: unknown ty {ty}')
    return pack, unpack

class Packer:
    def __init__(self):
        self.ba = bytearray()
        self.object_ids = []
    def pack(self, ty, val):
        pack, unpack = get_packer(ty)
        return pack(val, self)
    def finish(self):
        return self.ba, self.object_ids
    def write(self, x):
        return self.ba.extend(x)

class Unpacker:
    def __init__(self, ba, object_ids=None, twib=None, device_id=None):
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
        pack, unpack = get_packer(ty)
        return unpack(self)
    def unpack_and_finish(self, ty):
        ret = self.unpack(ty)
        assert len(self.fp.read()) == 0
        return ret

def fmt_result(rc):
    hi, lo = rc >> 9, rc & 0x1ff
    if lo == 0xef:
        return f'TWILI_RESULT({lo}'
    else:
        return '?'

class ResultError(Exception):
    def __init__(self, rc):
        self.rc = rc
    def __str__(self):
        return f'ResultError(rc={self.rc:#x} / {fmt_result(self.rc)})'

class Twib:
    message_hdr_types = (uint32_t, uint32_t, uint32_t, uint32_t, uint64_t, uint32_t, uint32_t)
    @classmethod
    async def create(cls, path='/opt/twib/twibd.sock'):
        self = cls()
        self.reader, self.writer = await asyncio.open_unix_connection(path)
        #self.reader, self.writer = await asyncio.open_connection('127.0.0.1', 9191)
        self.last_tag = 0
        self.response_futures = {}
        asyncio.create_task(self.read_task())
        self.meta = ITwibMetaInterface(self, 0, 0)
        return self

    def device_interface(self, dev_dict):
        return ITwibDeviceInterface(self, dev_dict[b'device_id'], 0)

    async def read_task(self):
        while True:
            #print('reading')
            h = await self.reader.read(32) # header size
            #print('!')
            (device_id, object_id, result_code, tag,
             payload_size, object_count, pad) = Unpacker(h).unpack_and_finish(Twib.message_hdr_types)
            print(f'response: device_id={device_id} object_id={object_id} result_code={result_code} tag={tag} payload_size={payload_size} object_count={object_count} pad={pad}')
            payload = await self.reader.read(payload_size)
            object_ids = []
            for i in range(object_count):
                object_ids.append(struct.unpack('<I', await self.reader.read(4))[0])
            unpacker = Unpacker(payload, object_ids, self, device_id)
            f = self.response_futures[tag]
            f.set_result((result_code, unpacker))
            del self.response_futures[tag]

    def new_tag(self):
        self.last_tag += 1
        assert self.last_tag <= 0xffffffff
        return self.last_tag

    async def request(self, device_id, object_id, command_id, in_types, out_types, *in_args):
        assert isinstance(in_types, tuple)
        tag = self.new_tag()
        payload_packer = Packer()
        payload_packer.pack(in_types, in_args)
        payload, objects = payload_packer.finish()
        print(f'request: device_id={device_id} object_id={object_id} command_id={command_id} tag={tag} payload_size={len(payload)}')
        assert len(objects) == 0
        outer_packer = Packer()
        pad = 0
        outer_packer.pack(Twib.message_hdr_types,
            (device_id, object_id, command_id, tag, len(payload), len(objects), pad))
        for obj in objects:
            outer_packer.pack(obj.object_id)
        outer_packer.write(payload)
        outer, _ = outer_packer.finish()
        #print(outer, len(outer))
        self.writer.write(outer)
        await self.writer.drain()
        f = asyncio.get_event_loop().create_future()
        self.response_futures[tag] = f
        rc, unpacker = await f
        if rc:
            raise ResultError(rc)
        ret = unpacker.unpack_and_finish(out_types)
        return ret

class RemoteObject:
    def __init__(self, twib, device_id, object_id):
        self.twib = twib
        self.device_id = device_id
        self.object_id = object_id
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
    def close(self):
        if self.object_id != 0:
            return self.request(0xffffffff, (), ())
        else:
            async def f(): pass
            return f()
    def request(self, command_id, in_types, out_types, *in_args):
        return self.twib.request(self.device_id, self.object_id, command_id, in_types, out_types, *in_args)
    @staticmethod
    def pack(val, packer):
        idx = len(packer.object_ids)
        packer.object_ids.append(val.object_id)
        packer.pack(uint32_t, idx)
    @classmethod
    def unpack(cls, unpacker):
        idx = unpacker.unpack(uint32_t)
        object_id = unpacker.object_ids[idx]
        return cls(unpacker.twib, unpacker.device_id, object_id)

class ITwibMetaInterface(RemoteObject):
    def list_devices(self):
        return self.request(10, (), MsgPack)

class ITwibDeviceInterface(RemoteObject):
    def list_processes(self):
        return self.request(14, (), List(ProcessReport))
    def open_active_debugger(self, pid):
        return self.request(19, (uint64_t,), ITwibDebugger, pid)

class ITwibDebugger(RemoteObject):
    def get_nso_infos(self):
        return self.request(19, (), List(NxoInfo))
    def get_nro_infos(self):
        return self.request(24, (), List(NxoInfo))
    def read_memory(self, addr, size):
        return self.request(11, (uint64_t, uint64_t), bytes, addr, size)
    def write_memory(self, addr, blob):
        return self.request(12, (uint64_t, bytes), (), addr, blob)

class Autorepr:
    def __repr__(self):
        return f'{self.__class__.__name__}({self.__dict__})'

class ProcessReport(Autorepr):
    pack = None
    @classmethod
    def unpack(cls, unpacker):
        ret = cls()
        ret.process_id = unpacker.unpack(uint64_t)
        ret.result = unpacker.unpack(uint32_t)
        pad = unpacker.unpack(uint32_t)
        ret.title_id = unpacker.unpack(uint64_t)
        ret.process_name = unpacker.unpack(Prim('12s')).rstrip(b'\0')
        ret.mmu_flags = unpacker.unpack(uint32_t)
        return ret

class NxoInfo(object):
    pack = None
    @classmethod
    def unpack(cls, unpacker):
        ret = cls()
        ret.build_id = unpacker.unpack(Prim('32s'))
        ret.address = unpacker.unpack(uint64_t)
        ret.size = unpacker.unpack(uint64_t)
        return ret
    def __repr__(self):
        return f'NxoInfo(build_id={binascii.hexlify(self.build_id)}, from=0x{self.address:016x}, to=0x{self.address+self.size:016x}, size=0x{self.size:x})'

if __name__ == '__main__':
    async def main():
        twib = await Twib.create()
        devs = await twib.meta.list_devices()
        print(devs)
        for dd in devs:
            async with twib.device_interface(dd) as dev:
                for proc in await dev.list_processes():
                    print(proc)
                    if proc.process_id == 0x82:
                        async with await dev.open_active_debugger(proc.process_id) as dbg:
                            nsos = await dbg.get_nso_infos()
                            print(nsos)
                            print(repr(await dbg.read_memory(nsos[0].address, 32)))
                            #print(await dbg.get_nro_infos())
    asyncio.run(main())
