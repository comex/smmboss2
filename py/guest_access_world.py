import functools, io, sys, struct, inspect, re, os

class GuestPtrMeta(type):
    def __matmul__(self, addr):
        # silly hack so that e.g. u32@0xdeadbeef is a valid expression
        # though it doesn't bind tightly - you would need parens in (Foo@0x1234).bar
        return self(addr)

@functools.total_ordering
class GuestPtr(metaclass=GuestPtrMeta):
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
    def dump(self, fp, indent, **opts):
        fp.write(f'{self.__class__.__name__}@{self.addr:#x}')
        if self.addr == 0:
            return
        try:
            val = self.get()
        except Exception as e:
            fp.write(f' => bad {e!r}')
        else:
            fp.write(f' => {val:#x}')

def make_GuestPrimPtr(code, class_name):
    code = '<'+code
    size = struct.calcsize(code)
    class GuestXPrimPtr(GuestPrimPtr):
        sizeof_star = size
        @staticmethod
        def decode_data(data):
            return struct.unpack(code, data[:size])[0]
        @staticmethod
        def encode_data(val):
            return struct.pack(code, val)
    GuestXPrimPtr.__name__ = class_name
    GuestXPrimPtr.code = code
    return GuestXPrimPtr

u8 = make_GuestPrimPtr('B', 'u8')
u16 = make_GuestPrimPtr('H', 'u16')
u32 = make_GuestPrimPtr('I', 'u32')
u64 = make_GuestPrimPtr('Q', 'u64')
s8 = make_GuestPrimPtr('b', 's8')
s16 = make_GuestPrimPtr('h', 's16')
s32 = make_GuestPrimPtr('i', 's32')
s64 = make_GuestPrimPtr('q', 's64')
f32 = make_GuestPrimPtr('f', 'f32')
f64 = make_GuestPrimPtr('d', 'f64')

usize = u64
ptr_size = usize.sizeof_star

class GuestPtrPtrBase(GuestPrimPtr):
    pass

@functools.lru_cache(None)
def ptr_to(ptr_ty):
    assert isinstance(ptr_ty, type)
    class GuestXPtrPtr(GuestPtrPtrBase):
        sizeof_star = ptr_size
        val_ty = ptr_ty
        @classmethod
        def decode_data(cls, data):
            return cls.val_ty(usize.decode_data(data))
        @classmethod
        def encode_data(cls, val):
            assert isinstance(val, cls.val_ty)
            return usize.encode_data(val.addr)
    GuestXPtrPtr.__name__ = f'ptr_to({ptr_ty.__name__})'
    return GuestXPtrPtr

GuestPtrPtr = ptr_to(GuestPtr)

class GuestArray(GuestPtr):
    def __init__(self, addr, ptr_ty=None, count=None):
        super().__init__(addr)
        if ptr_ty is not None:
            self.ptr_ty = ptr_ty
        if count is not None:
            self.count = count
    def __getitem__(self, n, unchecked=False):
        if isinstance(n, slice):
            assert not unchecked # todo
            assert n.step is None or n.step == 1
            start, stop = n.start, n.stop
            if start is None: start = 0
            if stop is None: stop = self.count
            if start < 0: start += self.count
            if stop < 0: start += self.count
            assert start <= stop
            return GuestArray(self.addr + start * self.ptr_ty.sizeof_star, self.ptr_ty, stop - start)
        item = self.ptr_at(n, unchecked=unchecked)
        if hasattr(item, 'get'):
            item = item.get() # xxx
        return item
    def get_unchecked(self, n):
        return self.__getitem__(n, unchecked=True)
    def __setitem__(self, n, val, unchecked=False):
        return self.ptr_at(n, unchecked=unchecked).set(val)
    def ptr_at(self, n, unchecked=False):
        if not unchecked and not (0 <= n < self.count):
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
    def get_all(self, decoder=None):
        # TODO: this should just be a decode_data impl combined with something else?
        count = self.count
        sizeof_elm = self.ptr_ty.sizeof_star
        if decoder is None:
            decoder = self.ptr_ty.decode_data
        raw_data = guest.read(self.base.addr, count * sizeof_elm)
        out = []
        for i in range(0, count * sizeof_elm, sizeof_elm):
            out.append(decoder(raw_data[i:i+sizeof_elm]))
        return out
    def cache_all(self):
        guest.cache_region(self.base.addr, self.count * self.ptr_ty.sizeof_star)
    def dump(self, fp, indent, **opts):
        count = self.count
        fp.write('array (%#x, count=%u):' % (self.addr, count))
        if self.addr == 0:
            fp.write(' (null)')
            return
        indent2 = indent + '  '
        for i in range(count):
            if not self.should_dump_ith(i):
                continue
            fp.write('\n%s[%d] = ' % (indent2, i))
            item = self[i]
            dump(item, fp, indent2, **opts)
            fp.write(',')
    def should_dump_ith(self, i):
        return True # subclass hook

def count4_ptr(ptr_ty):
    pp = ptr_to(ptr_ty)
    class CountPtr(GuestArray, GuestStruct):
        def __init__(self, addr):
            super().__init__(addr, ptr_ty)
        count = prop(0, u32)
        base = prop(ptr_size, pp)
        sizeof_star = 2 * ptr_size
        def __len__(self):
            return self.count
    return CountPtr

class GuestFixedArrayBase(GuestArray):
    pass

@functools.lru_cache(None)
def fixed_array(ptr_ty, count):
    class GuestFixedArray(GuestFixedArrayBase):
        def __init__(self, addr):
            super().__init__(addr, ptr_ty, count)
        sizeof_star = ptr_ty.sizeof_star * count
    GuestFixedArray.count = count
    GuestFixedArray.val_ptr_ty = ptr_ty
    GuestFixedArray.__name__ = f'fixed_array({ptr_ty.__name__}, {count})'
    return GuestFixedArray

class MyProperty(property):
    def __init__(self, offset, ptr_cls_or_f, dump=True, dump_deep=False, include_in_repr=False):
        assert isinstance(offset, int)
        self.offset = offset
        self.ptr_cls_or_f = ptr_cls_or_f
        self.must_call = not inspect.isclass(ptr_cls_or_f)
        self.dump = dump
        self.dump_deep = dump_deep
        self.include_in_repr = include_in_repr
        super().__init__(self.read, self.write)
    @property
    def ptr_cls(self):
        if self.must_call:
            self.ptr_cls_or_f = self.ptr_cls_or_f()
            self.must_call = False
        return self.ptr_cls_or_f
    def ptr(self, this):
        return self.ptr_cls(this.addr + self.offset)
    def read(self, this):
        return self.ptr(this).get()
    def write(self, this, value):
        return self.ptr(this).set(value)
    def dump_field(self, this, fp, indent, key, **opts):
        if not self.dump:
            return
        fp.write('\n%s%s: ' % (indent, key))
        val = self.read(this)
        val_ty = getattr(self.ptr_cls, 'val_ty', None)
        if val_ty is not None and issubclass(val_ty, GuestPtr) and not self.dump_deep:
            fp.write(repr(val))
        else:
            dump(val, fp, indent, **opts)
prop = MyProperty

def addrof(obj, prop):
    prop = getattr(type(obj), prop)
    assert isinstance(prop, MyProperty)
    return prop.ptr(obj)

@functools.lru_cache(None)
def offsetof(cls, prop):
    return addrof(cls(None, 0), prop).addr

def maybe_call(f):
    if inspect.isclass(f):
        return f
    else:
        return f()

def as_addr(obj_or_addr):
    if isinstance(obj_or_addr, GuestStruct):
        return obj_or_addr.addr
    else:
        return int(obj_or_addr) & 0xffffffffffffffff

class GuestStruct(GuestPtr):
    def dump(self, fp, indent, **opts):
        fp.write('%s (%#x):' % (self.__class__.__name__, self.addr))
        if self.addr == 0:
            fp.write(' (null)')
            return
        indent2 = indent + '  '
        for key, prop in self._properties():
            prop.dump_field(self, fp, indent2, key, **opts)
    def get(self):
        return self
    def set(self, val):
        raise Exception("can't set() struct")

    full_repr = False
    def __repr__(self):
        ret = super().__repr__()
        if self.full_repr:
            include_properties = self._properties()
        else:
            include_properties = self._include_in_repr_properties()
        if include_properties and self.addr != 0:
            subreprs = []
            for key, prop in include_properties:
                subreprs.append(f"{key}={getattr(self, key)!r}")
            ret += '({})'.format(', '.join(subreprs))
        return ret

    @classmethod
    @functools.cache
    def _include_in_repr_properties(cls):
        return [
            (key, prop) for (key, prop) in cls._properties()
            if prop.include_in_repr
        ]

    @classmethod
    @functools.cache
    def _decodable_properties(cls):
        return [
            (key, prop)
            for (key, prop) in cls._properties()
            if hasattr(prop.ptr_cls, 'decode_data')
        ]

    @classmethod
    @functools.cache
    def _properties(cls):
        return [
            (key, prop)
            for supercls in cls.mro()[::-1]
            for key, prop in supercls.__dict__.items()
            if isinstance(prop, MyProperty)
        ]

    @classmethod
    def _base_guest_struct(cls):
        base = cls.__bases__[0]
        if (issubclass(base, GuestStruct) and
            base is not GuestStruct):
            return base

    @classmethod
    def decode_data(cls, data):
        # TODO: why is this not autoreloading?
        ret = {}
        for key, prop in cls._decodable_properties():
            start = prop.offset
            assert start <= len(data)
            ret[key] = prop.ptr_cls.decode_data(data[start:])
        return ret

class GuestCString(GuestPtr):
    def sizeof_star(self):
        raise Exception('did you really mean to get sizeof(cstring)?')
    def get(self):
        addr = self.addr
        if addr == 0:
            return None
        return guest.read_cstr(self.addr)
    def as_str(self):
        return os.fsdecode(self.get())
    def __repr__(self):
        #return '%s %s' % (super().__repr__(), self.get())
        return repr(self.as_str())

class GuestPtrToMemberFunction(GuestStruct):
    # todo: we are not doing a good job distinguishing values and pointers.
    word1 = prop(0, usize)
    word2 = prop(8, usize)

    def resolve(self, obj) -> GuestPtr:
        word1, word2 = self.word1, self.word2
        if word2 & 1:
            vtable_offset = word1
            offset_to_vtable = word2 >> 1
            vtable = usize(obj.addr + offset_to_vtable).get()
            return GuestPtrPtr(vtable + vtable_offset).get()
        else:
            return GuestPtr(word1)

def dump(val, fp=sys.stdout, indent='', **opts):
    if hasattr(val, 'dump'):
        val.dump(fp, indent, **opts)
    elif isinstance(val, int):
        fp.write('%#x' % val)
    elif hasattr(val, '__iter__') and not isinstance(val, (list, tuple)):
        fp.write(f'iterable (type {type(val)}):')
        indent2 = indent + '  '
        for i, item in enumerate(val):
            fp.write(f'\n{indent2}[{i}] = ')
            dump(item, fp, indent2, **opts)
            fp.write(',')
    else:
        fp.write(repr(val))
    if indent == '':
        fp.write('\n') # pfft

class GuestPlusFakeStack:
    def __init__(self, backing):
        self.backing = backing

    def try_read(self, addr, size):
        if addr > 0x2000 and addr + size <= 0x10000:
            return b'\0' * size
        return self.backing.try_read(addr, size)

    def try_write(self, addr, data):
        raise Exception("should not try to write to this")

@functools.cache
def unicorn_regs():
    from dataclasses import dataclass
    import unicorn.arm64_const as ac
    @dataclass
    class Reg:
        const: int

    class XReg(Reg):
        def read(self, mu):
            return mu.reg_read(self.const)
        def write(self, mu, val):
            mu.reg_write(self.const, val)
    class SReg(Reg):
        def read(self, mu):
            return struct.unpack('<f', struct.pack('<I', mu.reg_read(self.const)))[0]
        def write(self, mu, val):
            mu.reg_write(self.const, struct.unpack('<I', struct.pack('<f', val))[0])
    uregs = {}
    for i in range(31):
        uregs[f'x{i}'] = XReg(getattr(ac, f'UC_ARM64_REG_X{i}'))
    for i in range(32):
        uregs[f's{i}'] = SReg(getattr(ac, f'UC_ARM64_REG_S{i}'))
    return uregs

class RegsWrap:
    def __init__(self, mu):
        self.__dict__['mu'] = mu
        self.__dict__['uregs'] = unicorn_regs()
    def get(self, reg):
        return self.uregs[reg].read(self.mu)
    __getattr__ = get
    def set(self, reg, val):
        self.uregs[reg].write(self.mu, val)
    __setattr__ = set

class Emu:
    pass

# barebones (for now)
def emulate_call(pc, verbose=False, slide=0, mm=None, **kwargs):
    if mm is not None:
        slide = mm.slide
        unslide = mm.unslide
    else:
        slide = unslide = lambda addr: addr
    import unicorn
    import unicorn.arm64_const as ac
    mu = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)

    from guest_access import CachingGuest
    fake_guest = CachingGuest(GuestPlusFakeStack(guest), imaginary_mode=True)

    fake_mem = {}

    def mmio_read_cb(uc, offset, size, data):
        ret = int.from_bytes(fake_guest.read(offset, size), 'little') 
        if verbose and 0:
            print(f">>> read {size} from {offset:#x} => {ret:#x}")
        return ret

    def mmio_write_cb(uc, offset, size, value, data):
        if verbose:
            print(f">>> write {value:#x} size {size} to {offset:#x}")
        fake_guest.write(offset, value.to_bytes(size, 'little'))

    ADDR = 0
    SIZE = 1 << 48
    mu.mmio_map(ADDR, SIZE, mmio_read_cb, None, mmio_write_cb, None)
    mu.mem_protect(ADDR, SIZE, unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE | unicorn.UC_PROT_EXEC)
    mu.reg_write(ac.UC_ARM64_REG_SP, 0x10000)
    mu.reg_write(ac.UC_ARM64_REG_LR, 0x1234)
    rw = RegsWrap(mu)
    for reg, val in kwargs.items():
        rw.set(reg, val)
    single_step = verbose or True
    max_insns = 300
    prev_reg_vals_raw = {}
    pc = slide(pc)
    for step in range(max_insns if single_step else 1):
        if verbose:
            print(f'pc={unslide(pc):#x}', end='')
            for name, reg in rw.uregs.items():
                val_raw = reg.read(mu)
                prev = prev_reg_vals_raw.get(name)
                if val_raw != prev:
                    val = rw.get(name)
                    if isinstance(val, int):
                        val = hex(val)
                    print(f' {name}={val}', end='')
                    prev_reg_vals_raw[name] = val_raw
            print()
        mu.emu_start(begin=pc, until=0x1234, timeout=10_000_000, count=1 if single_step else max_insns)
        pc = mu.reg_read(ac.UC_ARM64_REG_PC)
        if pc == 0x1234:
            break
    else:
        raise Exception("took too long")

    emu = Emu()
    emu.guest = fake_guest
    emu.regs = rw
    return emu

def guest_read_ptr(ty, addr):
    return ptr_to(ty)(addr).get()
