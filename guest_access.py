import functools, struct, os

class World:
    def _import(self, filename):
        filename = os.path.join(os.path.dirname(__file__), filename)
        code = compile(open(filename).read(), filename, 'exec')
        exec(code, self.__dict__)

@functools.total_ordering
class Guest:
    def __eq__(self, other):
        return self is other
    def __lt__(self, other):
        return id(self) < id(other)
    def read(self, addr, size):
        data = self.try_read(addr, size)
        if len(data) != size:
            raise Exception('only read %#x/%#x bytes @ %#x' % (len(data), size, addr))
        return data
    def write(self, addr, data):
        actual = self.try_write(addr, data)
        if actual != len(data):
            raise Exception('only wrote %#x/%#x bytes @ %#x' % (actual, len(data), addr))
    def _xreadwrite(fmt):
        size = struct.calcsize(fmt)
        def read(self, addr):
            return struct.unpack(fmt, self.read(addr, size))[0]
        def write(self, addr, val):
            return self.write(addr, struct.pack(fmt, val))
        return read, write
    read8, write8 = _xreadwrite('<B')
    read16, write16 = _xreadwrite('<H')
    read32, write32 = _xreadwrite('<I')
    read64, write64 = _xreadwrite('<Q')
    def read_ptr(self, ty, addr):
        return self.world.ptr_to(ty)(addr).get()

    def read_cstr(self, addr):
        data = b''
        while b'\0' not in data:
            size = 0x20 - (addr & 0x1f)
            #print(hex(addr), hex(size), data)
            data += self.read(addr, size)
            addr += size
        #print(repr(data))
        return data[:data.index(b'\0')]

    def __hash__(self):
        return id(self)

    def make_world(self):
        world = World()
        world.guest = self
        world._import('guest_access_world.py')
        return world

class CachingGuest(Guest):
    def __init__(self, backing, imaginary_mode=False):
        super().__init__()
        self.backing = backing
        self.chunk_size = 0x100
        self.cache = {}
        self.active_count = 1 if imaginary_mode else 0
        # if imaginary_mode is True, we won't actually write back any changes,
        # and this serves as an overlay on top of real memory - used for
        # emulation
        self.imaginary_mode = imaginary_mode

    def try_read(self, addr, size):
        if not self.active_count:
            assert not self.imaginary_mode
            return self.backing.try_read(addr, size)
        ret = b''
        chunk_addr = addr - (addr % self.chunk_size)
        need_read_start = None
        while chunk_addr < addr + size:
            chunk_data = self.cache.get(chunk_addr)
            if chunk_data is None:
                read_size = self.chunk_size
                while (chunk_addr + read_size) < addr + size and \
                    (chunk_addr + read_size) not in self.cache:
                    read_size += self.chunk_size
                read_data = self.backing.try_read(chunk_addr, read_size)
                if len(read_data) != read_size:
                    break
                for off in range(0, read_size, self.chunk_size):
                    self.cache[chunk_addr + off] = bytearray(read_data[off:off+self.chunk_size])
                ret += read_data
                chunk_addr += read_size
            else:
                ret += chunk_data
                chunk_addr += self.chunk_size
        off = addr % self.chunk_size
        return ret[off:off+size]

    def try_write(self, addr, data):
        size = len(data)
        chunk_addr = addr - (addr % self.chunk_size)
        if self.imaginary_mode:
            readable_size = len(self.try_read(addr, size))
            size = readable_size
            data = data[:size]
        while chunk_addr < addr + size:
            chunk = self.cache.get(chunk_addr)
            if self.imaginary_mode:
                assert chunk is not None
            if chunk is not None:
                lo = max(addr, chunk_addr)
                hi = min(addr + size, chunk_addr + self.chunk_size)
                self.cache[chunk_addr][lo - chunk_addr : hi - chunk_addr] = data[lo - addr : hi - addr]
            chunk_addr += self.chunk_size
        if self.imaginary_mode:
            return size
        else:
            return self.backing.try_write(addr, data)

    def __enter__(self):
        self.active_count += 1
    def __exit__(self, exc_type, exc_value, traceback):
        self.active_count -= 1
        if self.active_count == 0:
            self.cache = {}

    def __getattr__(self, attr):
        return getattr(self.backing, attr)

