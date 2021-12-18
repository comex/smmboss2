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
            raise Exception('only read %#x/%#x bytes @ %#x' % (actual, len(data), addr))
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
    def __hash__(self):
        return id(self)

    def make_world(self):
        world = World()
        world.guest = self
        world._import('guest_access_world.py')
        return world

class CachingGuest(Guest):
    def __init__(self, backing):
        super().__init__()
        self.backing = backing
        self.chunk_size = 0x100
        self.cache = {}
        self.active_count = 0
    def read(self, addr, size):
        if not self.active_count:
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
                read_data = self.backing.read(chunk_addr, read_size)
                assert len(read_data) == read_size
                for off in range(0, read_size, self.chunk_size):
                    self.cache[chunk_addr + off] = read_data[off:off+self.chunk_size]
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
        while chunk_addr < addr + size:
            if chunk_addr in self.cache:
                del self.cache[chunk_addr]
            chunk_addr += self.chunk_size
        return self.backing.try_write(addr, data)

    def __enter__(self):
        self.active_count += 1
    def __exit__(self, exc_type, exc_value, traceback):
        self.active_count -= 1
        if self.active_count == 0:
            self.cache = {}

    def __getattr__(self, attr):
        return getattr(self.backing, attr)

