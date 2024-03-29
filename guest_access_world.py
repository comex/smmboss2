import functools, io, sys, struct, inspect
@functools.total_ordering
class GuestPtr:
    def __init__(self, addr):
        self.addr = as_addr(addr)
    def __repr__(self):
        return hex(self.addr)
    def __bool__(self):
        return bool(self.addr)
    def __hash__(self):
        return hash(self.addr)
    def __eq__(self, other):
        assert isinstance(other, GuestPtr)
        return self.addr == other.addr
    def __lt__(self, other):
        assert isinstance(other, GuestPtr)
        return self.addr < other.addr
    def __repr__(self):
        return '%s@%#x' % (self.__class__.__name__, self.addr)
    def cast(self, ty):
        return ty(self.addr)
    def raw_offset(self, offset, ty):
        return ty(as_addr(self.addr + offset))
    def dump_str(self):
        fp = io.StringIO()
        dump(self, fp)
        return fp.getvalue().rstrip('\n')

class GuestPrimPtr(GuestPtr):
    def get(self):
        return self.decode_data(guest.read(self.addr, self.sizeof_star))
    def set(self, val):
        return guest.write(self.addr, self.encode_data(val))

def make_GuestPrimPtr(code):
    code = '<'+code
    size = struct.calcsize(code)
    class GuestXPrimPtr(GuestPrimPtr):
        sizeof_star = size
        @staticmethod
        def decode_data(data):
            return struct.unpack(code, data)[0]
        @staticmethod
        def encode_data(val):
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
        def decode_data(data):
            return maybe_call(ptr_ty)(usize.decode_data(data))
        @staticmethod
        def encode_data(val):
            assert isinstance(val, ptr_ty)
            return usize.encode_data(val.addr)
    return GuestXPtrPtr

GuestPtrPtr = ptr_to(GuestPtr)

class GuestArray(GuestPtr):
    def __init__(self, addr, ptr_ty, count):
        super().__init__(addr)
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
            return GuestArray(self.addr + start * self.ptr_ty.sizeof_star, self.ptr_ty, stop - start)
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
        return self.ptr_ty(self.addr)
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
        raw = guest.read(self.base.addr, count * sizeof_elm)
        out = []
        for i in range(0, count * sizeof_elm, sizeof_elm):
            out.append(self.ptr_ty.decode_data(raw[i:i+sizeof_elm]))
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
        def __init__(self, addr):
            GuestPtr.__init__(self, addr)
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
        def __init__(self, addr):
            super().__init__(addr, ptr_ty, count)
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
        return maybe_call(ptr_cls_f)(self.addr + offset)
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
            data += guest.read(addr, size)
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

