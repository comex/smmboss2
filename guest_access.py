import functools, inspect, struct, io, sys

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
        return ptr_to(ty)(self, addr).get()
    def __hash__(self):
        return id(self)

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

@functools.total_ordering
class GuestPtr:
    def __init__(self, guest, addr):
        self.guest = guest
        self.addr = as_addr(addr)
    def __repr__(self):
        return hex(self.addr)
    def __bool__(self):
        return bool(self.addr)
    def __hash__(self):
        return hash(self.addr)
    def __eq__(self, other):
        assert isinstance(other, GuestPtr)
        return self.guest == other.guest and \
            self.addr == other.addr
    def __lt__(self, other):
        assert isinstance(other, GuestPtr)
        return self.guest < other.guest or \
            (self.guest == other.guest and self.addr < other.addr)
    def __repr__(self):
        return '%s@%#x' % (self.__class__.__name__, self.addr)
    def cast(self, ty):
        return ty(self.guest, self.addr)
    def raw_offset(self, offset, ty):
        return ty(self.guest, as_addr(self.addr + offset))
    def dump_str(self):
        fp = io.StringIO()
        dump(self, fp)
        return fp.getvalue().rstrip('\n')

class GuestPrimPtr(GuestPtr):
    def get(self):
        return self.decode_data(self.guest.read(self.addr, self.sizeof_star), self.guest)
    def set(self, val):
        return self.guest.write(self.addr, self.encode_data(val, self.guest))

def make_GuestPrimPtr(code):
    code = '<'+code
    size = struct.calcsize(code)
    class GuestXPrimPtr(GuestPrimPtr):
        sizeof_star = size
        @staticmethod
        def decode_data(data, guest):
            return struct.unpack(code, data)[0]
        @staticmethod
        def encode_data(val, guest):
            return struct.pack(code, val)
    return GuestXPrimPtr

u8 = make_GuestPrimPtr('B')
u16 = make_GuestPrimPtr('H')
u32 = make_GuestPrimPtr('I')
u64 = make_GuestPrimPtr('Q')
s8 = make_GuestPrimPtr('b')
s16 = make_GuestPrimPtr('h')
s32 = make_GuestPrimPtr('i')
s64 = make_GuestPrimPtr('q')
f32 = make_GuestPrimPtr('f')
f64 = make_GuestPrimPtr('d')

usize = u64
ptr_size = usize.sizeof_star

@functools.lru_cache(None)
def ptr_to(ptr_ty):
    class GuestXPtrPtr(GuestPrimPtr):
        sizeof_star = ptr_size
        val_ty = ptr_ty
        @staticmethod
        def decode_data(data, guest):
            return maybe_call(ptr_ty)(guest, usize.decode_data(data, guest))
        @staticmethod
        def encode_data(val, guest):
            assert isinstance(val, ptr_ty)
            return usize.encode_data(val.addr, guest)
    return GuestXPtrPtr

GuestPtrPtr = ptr_to(GuestPtr)

class GuestArray(GuestPtr):
    def __init__(self, guest, addr, ptr_ty, count):
        super().__init__(guest, addr)
        self.ptr_ty = ptr_ty
        self.count = count
    def __getitem__(self, n):
        if isinstance(n, slice):
            assert n.step is None or n.step == 1
            start, stop = n.start, n.stop
            if start is None: start = 0
            if stop is None: stop = self.count
            if start < 0: start += self.count
            if stop < 0: start += self.count
            assert start <= stop
            return GuestArray(self.guest, self.addr + start * self.ptr_ty.sizeof_star, self.ptr_ty, stop - start)
        item = self.ptr_at(n)
        if hasattr(item, 'get'):
            item = item.get() # xxx
        return item
    def __setitem__(self, n, val):
        return self.ptr_at(n).set(val)
    def ptr_at(self, n):
        if not (0 <= n <= self.count):
            raise IndexError
        return self.base.raw_offset(self.ptr_ty.sizeof_star * n, self.ptr_ty)
    def __iter__(self):
        for i in range(self.count):
            yield self[i]
    @property
    def base(self):
        return self.ptr_ty(self.guest, self.addr)
    def __len__(self):
        return self.count
    @property
    def sizeof_star(self):
        return self.count * self.ptr_ty.sizeof_star
    def get(self):
        return self
    def get_all(self):
        count = self.count
        sizeof_elm = self.ptr_ty.sizeof_star
        raw = self.guest.read(self.base.addr, count * sizeof_elm)
        out = []
        for i in range(0, count * sizeof_elm, sizeof_elm):
            out.append(self.ptr_ty.decode_data(raw[i:i+sizeof_elm], self.guest))
        return out
    def dump(self, fp, indent):
        count = self.count
        fp.write('array (%#x, count=%x):' % (self.addr, count))
        if self.addr == 0:
            fp.write(' (null)')
            return
        indent2 = indent + '  '
        for i in range(count):
            fp.write('\n%s[%d] = ' % (indent2, i))
            item = self[i]
            dump(item, fp, indent2)
            fp.write(',')

def count4_ptr(ptr_ty):
    pp = ptr_to(ptr_ty)
    class CountPtr(GuestArray, GuestStruct):
        def __init__(self, guest, addr):
            GuestPtr.__init__(self, guest, addr)
            self.ptr_ty = ptr_ty
        count = prop(0, u32)
        base = prop(ptr_size, pp)
        sizeof_star = ptr_size
        def __len__(self):
            return self.count
    return CountPtr

@functools.lru_cache(None)
def fixed_array(ptr_ty, count):
    class GuestFixedArray(GuestArray):
        def __init__(self, guest, addr):
            super().__init__(guest, addr, ptr_ty, count)
    return GuestFixedArray

class MyProperty(property):
    def __init__(self, fget, fset, fptr, **kwargs):
        super().__init__(fget, fset, **kwargs)
        self.fptr = fptr

def addrof(obj, prop):
    prop = type(obj).__dict__[prop]
    assert isinstance(prop, MyProperty)
    return prop.fptr(obj)

@functools.lru_cache(None)
def offsetof(cls, prop):
    return addrof(cls(None, 0), prop).addr

def maybe_call(f):
    if inspect.isclass(f):
        return f
    else:
        return f()

def prop(offset, ptr_cls_f):
    assert isinstance(offset, int)
    def ptr(self):
        return maybe_call(ptr_cls_f)(self.guest, self.addr + offset)
    def read(self):
        return ptr(self).get()
    def write(self, value):
        return ptr(self).set(value)
    return MyProperty(read, write, ptr)

def as_addr(obj_or_addr):
    if isinstance(obj_or_addr, GuestStruct):
        return obj_or_addr.addr
    else:
        return int(obj_or_addr) & 0xffffffffffffffff

class GuestStruct(GuestPtr):
    def dump(self, fp, indent):
        fp.write('%s (%#x):' % (self.__class__.__name__, self.addr))
        if self.addr == 0:
            fp.write(' (null)')
            return
        indent2 = indent + '  '
        for cls in type(self).mro()[::-1]:
            for key, prop in cls.__dict__.items():
                if isinstance(prop, MyProperty):
                    fp.write('\n%s%s: ' % (indent2, key))
                    dump(getattr(self, key), fp, indent2)
    def get(self):
        return self
    def set(self, val):
        raise Exception("can't set() struct")

class GuestCString(GuestPtr):
    def sizeof_star(self):
        raise Exception('did you really mean to get sizeof(cstring)?')
    def get(self):
        addr = self.addr
        if addr == 0:
            return None
        data = b''
        while b'\0' not in data:
            size = 0x20 - (addr & 0x1f)
            #print(hex(addr), hex(size), data)
            data += self.guest.read(addr, size)
            addr += size
        #print(repr(data))
        return data[:data.index(b'\0')]
    def as_str(self):
        return self.get().decode('utf-8')
    def __repr__(self):
        #return '%s %s' % (super().__repr__(), self.get())
        return repr(self.get())

def dump(val, fp=sys.stdout, indent=''):
    if hasattr(val, 'dump'):
        val.dump(fp, indent)
    elif isinstance(val, int):
        fp.write('%#x' % val)
    else:
        fp.write(repr(val))
    if indent == '':
        fp.write('\n') # pfft

