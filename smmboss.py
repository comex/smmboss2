#!/usr/bin/env python3

from guest_access import *
import socket, struct, sys
from threading import Lock
from binascii import hexlify
import time

class RPLSegment:
    def __init__(self, data):
        self.addr, self.slide, self.size = struct.unpack('>III', data)
    def __repr__(self):
        return 'RPLSegment(addr=%#x, slide=%#x, slide=%#x)' % (self.addr, self.slide, self.size)
class RPL:
    def __init__(self, data, guest):
        name_addr, = struct.unpack('>I', data[:4])
        self.name = GuestCString(guest, name_addr)
        self.text = RPLSegment(data[4:16])
        self.data = RPLSegment(data[16:28])
        self.rodata = RPLSegment(data[28:40])
    def __repr__(self):
        return 'RPL(name=%r, text=%r, data=%r, rodata=%r)' % (self.name, self.text, self.data, self.rodata)

def to_addr(x):
    if hasattr(x, 'addr'):
        return x.addr
    else:
        return x

class MemrwGuest(Guest):
    def __init__(self, host, port):
        self.sock = socket.socket()
        self.sock.connect((host, port))
        self.lock = Lock()
        self.verbose = False
        self.chunk_size = 0x800
        self.rpls = self.read_rpl_info()
        rpx = self.rpls[-2]
        assert rpx.name.as_str().endswith('.rpx')
        self.text_slide = rpx.text.slide
        self.data_slide = rpx.data.slide

    @classmethod
    def with_hostport(cls, hostport):
        if ':' not in hostport:
            raise ValueError('not in host:port format: %r' % (hostport,))
        host, port = hostport.rsplit(':', 1)
        port = int(port)
        return cls(host, port)
    def slide_text(self, addr):
        return (addr + self.text_slide) & 0xffffffff
    def unslide_text(self, addr):
        return (to_addr(addr) - self.text_slide) & 0xffffffff
    def slide_data(self, addr):
        return (addr + self.data_slide) & 0xffffffff
    def unslide_data(self, addr):
        return (to_addr(addr) - self.data_slide) & 0xffffffff

    def recvall(self, size):
        data = b''
        while len(data) < size:
            chunk = self.sock.recv(size)
            if chunk == '':
                raise Exception("recvall: got ''")
            data += chunk
            assert len(data) <= size
        return data
    def try_read(self, addr, size):
        data = b''
        while size:
            assert size >= 0
            chunk_size = min(size, self.chunk_size)
            chunk_data = self.try_read_chunk(addr, chunk_size)
            data += chunk_data
            addr += len(chunk_data)
            size -= len(chunk_data)
            if len(chunk_data) < chunk_size:
                break
        return data
    def try_write(self, addr, data):
        actual = 0
        while actual < len(data):
            chunk_size = min(len(data) - actual, self.chunk_size)
            chunk_actual = self.try_write_chunk(addr, data[actual:actual+chunk_size])
            addr += chunk_actual
            actual += chunk_actual
            if chunk_actual < chunk_size:
                break
        return actual

    def try_read_chunk(self, addr, size):
        assert size <= 0x100000
        with self.lock:
            if self.verbose:
                print('try_read(%#x, %#x)' % (addr, size), end='')
                sys.stdout.flush()
            self.sock.sendall(struct.pack('>4sIII', b'MEMQ', 0, addr, size))
            magic, actual = struct.unpack('>4sI', self.recvall(8))
            self.check_magic(magic)
            ret = self.recvall(actual)
            if self.verbose:
                print(' -> %s' % (hexlify(ret),))
            return ret
    def try_write_chunk(self, addr, data):
        with self.lock:
            if self.verbose:
                print('try_read(%#x, %s)' % (addr, hexlify(data)), end='')
            self.sock.sendall(struct.pack('>4sIII', b'MEMQ', 1, addr, len(data)) + data)
            magic, actual = struct.unpack('>4sI', self.recvall(8))
            self.check_magic(magic)
            if self.verbose:
                print(' -> %s' % (actual,))
            assert actual <= len(data)
            return actual
    def read_rpl_info(self):
        with self.lock:
            if self.verbose:
                print('read_rpl_info()', end='')
                sys.stdout.flush()
            self.sock.sendall(struct.pack('>4sIII', b'MEMQ', 2, 0, 0))
            magic, actual = struct.unpack('>4sI', self.recvall(8))
            self.check_magic(magic)
            ret = self.recvall(actual)
            assert actual % 40 == 0
        rpls = []
        for i in range(0, actual, 40):
            rpl = RPL(ret[i:i+40], self)
            if self.verbose:
                print(rpl)
            rpls.append(rpl)
        return rpls
    def check_magic(self, magic):
        if magic == b'OOM!':
            raise Exception('stub was out of memory')
        assert magic == b'MEMA'

class Point2D(GuestStruct):
    x = prop(0, f32)
    y = prop(4, f32)
    sizeof_star = 8
class Point3D(Point2D):
    z = prop(8, f32)
    sizeof_star = 12
class Size2D(GuestStruct):
    w = prop(0, f32)
    h = prop(4, f32)
    sizeof_star = 8

class Rect(GuestStruct):
    min = prop(0, Point2D)
    max = prop(8, Point2D)
    sizeof_star = 0x10

class TT2(GuestStruct):
    cb_direct = prop(0xc, u32)
    sizeof_star = 0x10

class TransThing(GuestStruct):
    tt2_cur = fixed_array(ptr_to(TT2), 10)
    tt2_avail = fixed_array(ptr_to(TT2), 47)
    sizeof_star = 0xe4

class State(GuestStruct):
    name = prop(0, ptr_to(GuestCString))
    #name_ptr = prop(0, u32)
    not_name_len = prop(8, u32)
    #@property
    #def name(self):
    #    name = self.guest.read(self.name_ptr, self.name_len)
    #    name = name[:name.index(b'\0')]
    #    return name
    sizeof_star = 0x24
    def dump(self, fp, indent):
        fp.write('state(name=%r)' % (self.name,))

class GuestMethodPtr(GuestStruct):
    offset_to_this = prop(0, u16)
    vtable_idx = prop(2, u16)
    callback_or_offset_to_vt = prop(4, u32)
    sizeof_star = 8
    def target_for(self, obj):
        vtable_idx = self.vtable_idx
        callback_or_offset_to_vt = self.callback_or_offset_to_vt
        this = obj.raw_offset(self.offset_to_this, GuestPtr)
        if vtable_idx == 0 and callback_or_offset_to_vt == 0:
            return None, None
        elif vtable_idx >= 0x8000:
            return this, callback_or_offset_to_vt
        else:
            vtable = this.raw_offset(callback_or_offset_to_vt, GuestPtrPtr).get()
            callback = vtable.raw_offset(8 * vtable_idx + 4, u32).get()
            return this, callback

class StateMgr(GuestStruct):
    counter = prop(8, u32)
    state = prop(0xc, u32)
    oldstate = prop(0x10, u32)
    unk_count = prop(0x18, u32)
    state_list = prop(0x20, count_ptr(State))
    target_obj = prop(0x28, GuestPtrPtr)
    cb1s = prop(0x2c, count_ptr(GuestMethodPtr))
    cb2s = prop(0x34, count_ptr(GuestMethodPtr))
    cb3s = prop(0x3c, count_ptr(GuestMethodPtr))
    def print_cbs(self):
        target_obj = self.target_obj
        print('# target_obj=%s; state=0x%x oldstate=0x%x counter=0x%x' % (target_obj, self.state, self.oldstate, self.counter))
        names = []
        for i in range(len(self.state_list)):
            name = self.state_list[i].name
            names.append(name)
            print('# %#x: %s' % (i, name))
        for i, name in enumerate(names):
            for kind in ['cb1', 'cb2', 'cb3']:
                meth = getattr(self, kind + 's')[i]
                _, target = meth.target_for(target_obj)
                if target is None:
                    continue
                target = self.guest.unslide_text(target)
                print('MakeName(%#x, "ZZ_%s_%s_%x"); MakeFunction(%#x)' % (target, kind, name.as_str(), i, target))

class Killer(GuestStruct):
    statemgr = prop(0x28, StateMgr)

class Entity(GuestStruct):
    allocator = prop(0, GuestPtrPtr)
    model = prop(4, GuestPtrPtr)
    idbits = prop(0x14, s32)
    objrec = prop(0x18, lambda: ptr_to(ObjRec))
    min_y = prop(0x94, f32)
    vtable = prop(0xb4, GuestPtrPtr)
    sizeof_star = 0xb8

class EditEntity(Entity):
    rect = prop(0xc8, Rect)
    eapd = prop(0x304, lambda: ptr_to(EditActorPlacementData))

class PYES(Entity):
    loc = prop(0xb8, Point3D)
    houvelo = prop(0xc4, Point3D)
    influence_from_moving_platform = prop(0xdc, Point2D)
    gravity = prop(0x108, f32)
    player_idx = prop(0x110, s32)
    size1 = prop(0x114, Point2D)
    size2 = prop(0x11c, Point2D)
    unsize = prop(0x12c, Point2D)
    despawn_outsets = prop(0x134, fixed_array(f32, 4))

    old_loc = prop(0x308, Point3D)
    another_loc = prop(0x314, Point3D)
    stackid = prop(0x334, s32)
    tower_idx = prop(0x338, s32)

    sizeof_star = 0x358

class Player(PYES):
    sizeof_star = 0x2264
    flags_430 = prop(0x430, u32)
    flags_434 = prop(0x434, u32)
    statemgr_main = prop(0x358, StateMgr)
    statemgr_demo = prop(0x39c, StateMgr)
    statemgr_mantanim = prop(0x3e0, StateMgr)
    flags428 = prop(0x428, u32)
    other_flags = prop(0x42c, u32)
    jumpstate = prop(0x438, u32)
    other_jumpstate = prop(0x450, u32)
    flags = prop(0x544, u32)
    killer = prop(0x1de8+0xd4, ptr_to(Killer))
    timer_1dfc = prop(0x1dfc, u32)
    timer_1e00 = prop(0x1e00, u32)
    timer_1e04 = prop(0x1e04, u32)
    timer_1e4c = prop(0x1e4c, u32)
    timer_1e68 = prop(0x1e68, u32)
    timer_1e6c = prop(0x1e6c, u32)
    timer_1edc = prop(0x1edc, u32)
    transthing = prop(0x1f10, ptr_to(TransThing))
    timer_2004 = prop(0x2004, u32)
    timer_2008 = prop(0x2008, u32)
    timer_2020 = prop(0x2020, u32)
    timer_2024 = prop(0x2024, u32)
    timer_2038 = prop(0x2038, u32)
    timer_203c = prop(0x203c, u32)
    timer_2044 = prop(0x2044, u32)
    timer_204c = prop(0x204c, u32)
    timer_2050 = prop(0x2050, u32)
    timer_2054 = prop(0x2054, u32)
    timer_2068 = prop(0x2068, u32)
    timer_2074 = prop(0x2074, u32)
    timer_2078 = prop(0x2078, u32)
    timer_207c = prop(0x207c, u32)
    timer_2088 = prop(0x2088, u32)
    timer_2098 = prop(0x2098, u32)
    timer_209c = prop(0x209c, u32)
    timer_20a0 = prop(0x20a0, u32)
    timer_20ac = prop(0x20ac, u32)
    timer_20c0 = prop(0x20c0, u32)
    timer_20c8 = prop(0x20c8, u32)
    timer_20d0 = prop(0x20d0, u32)
    timer_20d4 = prop(0x20d4, u32)
    timer_20d8 = prop(0x20d8, u32)
    timer_20dc = prop(0x20dc, u32)
    timer_20e4 = prop(0x20e4, u32)
    timer_20e8 = prop(0x20e8, u32)
    timer_20ec = prop(0x20ec, u32)
    cape_related = prop(0x2100, u32)
    star_maybe = prop(0x220c, u32) # ?
    timer_2104 = prop(0x2104, u32)
    timer_2108 = prop(0x2108, u32)
    timer_210c = prop(0x210c, u32)
    timer_2114 = prop(0x2114, u32)
    timer_2118 = prop(0x2118, u32)
    timer_21bc = prop(0x21bc, u32)

class ObjLink(GuestStruct):
    next = prop(0, lambda: ptr_to(ObjLink)) # or null
    this = prop(4, ptr_to(GuestStruct))
    free = prop(8, u32)

global_root = lambda guest: ObjLink(guest, guest.read32(guest.slide_data(0x1036C1B0)))

class SpawnRect(GuestStruct):
    x = prop(0x00, u32)
    y = prop(0x04, u32)
    halfwidth = prop(0x08, u32)
    halfheight = prop(0x0c, u32)
    bits = prop(0x10, u16)

class ObjRec(GuestStruct):
    vt = prop(4, u32)
    ctor = prop(8, u32)
    idee = prop(0xc, u32)
    spawn_rect = prop(0x14, ptr_to(SpawnRect))
    name = prop(0x20, ptr_to(GuestCString))
    @functools.lru_cache(None)
    def get_name(self):
        return '%s(%x)' % (self.name.as_str(), self.idee)
    @staticmethod
    @functools.lru_cache(None)
    def by_idee(idee, guest):
        objrecs_by_idee = fixed_array(ptr_to(ObjRec), 0xee)(guest, guest.slide_data(0x101CF5B0))
        return objrecs_by_idee[idee]

@functools.lru_cache(None)
def exported_type_to_idee(n, guest):
    array = fixed_array(u32, 70)(guest, guest.slide_data(0x103354FC))
    return array[n]


class AllocLink(GuestStruct):
    prev = prop(0, ptr_to(lambda: AllocLink))
    next = prop(4, ptr_to(lambda: AllocLink))
class AllocTracker(AllocLink):
    obj_count = prop(8, u32)
    link_offset = prop(0xc, u32)
    sizeof_star = 0x10
    def iter_allocs(self):
        link_offset = self.link_offset
        link = self.next
        while link != self:
            next = link.next
            yield link.raw_offset(-link_offset, GuestPtr)
            link = next
    def iter_allocs_rev(self):
        link_offset = self.link_offset
        link = self.prev
        while link != self:
            prev = link.prev
            yield link.raw_offset(-link_offset, GuestPtr)
            link = prev


class MP5(GuestStruct):
    pointers = prop(0, count_ptr(ptr_to(Entity)))

class FancyString(GuestStruct):
    cstr = prop(0, ptr_to(GuestCString))
    vtable = prop(4, u32)

class HeapSuper(GuestStruct):
    name = prop(0x10, FancyString)

class Heap(HeapSuper):
    elm_size = prop(0x94, u32)
    range_start = prop(0x98, u32)
    range_size = prop(0x9c, u32)
    free_size = prop(0xa0, u32)
    first_free = prop(0xa4, u32)

class MakesPlayerObj(GuestStruct):
    tracker = prop(0x48f44, AllocTracker)
    tracker2 = prop(0x48f54, AllocTracker)
    tracker3 = prop(0x48f64, AllocTracker)
    mp5 = prop(0x527ec, MP5)
    heaps = prop(0x18, fixed_array(ptr_to(Heap), (0x50 - 0x18)//4))
    @staticmethod
    def get(guest):
        return MakesPlayerObj(guest, guest.read32(guest.slide_data(0x10194B08)))

class Spawner(GuestStruct):
    counts = prop(0xa04, fixed_array(u32, 6))
    @staticmethod
    def get(guest):
        return Spawner(guest, guest.read32(guest.slide_data(0x10194B00)))

class SomeCoiListSub1(GuestStruct):
    tracker = prop(0, AllocTracker)
    counts = prop(0xa40, fixed_array(u32, 0x24//4))
    sizeof_star = 0xa6c
class SomeCoiList(GuestStruct):
    sub1s = prop(0x18, fixed_array(SomeCoiListSub1, 2))
    @staticmethod
    def get_ptr(guest):
        return ptr_to(SomeCoiList)(guest, guest.slide_data(0x10195644))

class GroundsTrackers(GuestStruct):
    trackers = prop(0x10, fixed_array(AllocTracker, 7))
    @staticmethod
    def get_ptr(guest):
        return ptr_to(GroundsTrackers)(guest, guest.slide_data(0x10194F34))

class CdtObjectInternal(GuestStruct):
    x = prop(0, f32)
    y = prop(4, f32)
    q = prop(8, f32)
    width = prop(0xc, f32)
    height = prop(0x10, f32)
    my_flags = prop(0x14, u32)
    child_flags = prop(0x18, u32)
    extdata = prop(0x1c, u32)
    my_type = prop(0x20, u8)
    child_type = prop(0x21, u8)
    link_id = prop(0x22, u16)
    effect_id = prop(0x24, u16)
    my_transform_id = prop(0x26, u8)
    child_transform_id = prop(0x27, u8)
    sizeof_star = 0x30

    def desc(self):
        objrec = ObjRec.by_idee(exported_type_to_idee(self.my_type, self.guest), self.guest)
        return f'COI@{self.addr:#x}: type={objrec.name}(e:{self.my_type:x}) pos=({self.x}, {self.y}, {self.q}) size=({self.width, self.height}) flags={self.my_flags:x} ...'

class EditActorPlacementData(CdtObjectInternal):
    category = prop(0x108, u32)
    edit_entity = prop(0x80, ptr_to(EditEntity))
    bgunitgroup_idee = prop(0xfc, u32)
    sizeof_star = 0x198

class CourseThing(GuestStruct):

    course_bounds = prop(0x60, Rect)
    alt_bounds = prop(0x70, Rect)
    course_size = prop(0x80, Point2D)
    center = prop(0x88, Point2D)
    camera_rect = prop(0x90, Rect)
    alt_left = prop(0xa0, f32)
    alt_right = prop(0xa8, f32)
    zoom = prop(0xb8, f32)
    def get_ptr(guest):
        return ptr_to(CourseThing)(guest, guest.slide_data(0x1019B2EC))

class MP2SubBucket(GuestStruct):
    idee = prop(0x00, u32)
    x = prop(0x04, f32)
    y = prop(0x08, f32)
    f_c = prop(0x0c, f32)
    f_10 = prop(0x10, f32)
    stackid = prop(0x18, u16)
    idx_in_tower = prop(0x1c, u32)
    f20 = prop(0x20, u32)
    f24 = prop(0x24, u32)
    flags = prop(0x34, u8)
    sub_idee = prop(0x50, u32)
    link = prop(0x5c, AllocLink)

class CTracker(GuestStruct):
    last_used = prop(0x00, ptr_to(AllocLink))
    first_used = prop(0x04, ptr_to(AllocLink))
    used_count = prop(0x08, u32)
    also_buckets_ptr = prop(0x0c, u32)
    buckets_ptr = prop(0x10, u32)
    count = prop(0x14, u32)
    def iter_allocs(self):
        link = self.first_used
        offset = offsetof(self.item_cls, 'link')
        while link != self:
            yield link.raw_offset(-offset, self.item_cls)
            link = link.next

class MP2Sub(CTracker):
    item_cls = MP2SubBucket

class MP2(GuestStruct):
    sub = prop(0x10, MP2Sub)
    def get_ptr(guest):
        return ptr_to(MP2)(guest, guest.slide_data(0x10194B10))


class StrBinaryTree(GuestStruct):
    left = prop(0, ptr_to(lambda: StrBinaryTree))
    right = prop(4, ptr_to(lambda: StrBinaryTree))
    str = prop(0xc, ptr_to(GuestCString))
    def print(self):
        left, right, str = self.left, self.right, self.str
        if left:
            left.print()
        print(f'{self.addr:#x}: {str}')
        if right:
            right.print()

class LinkedCOI(CdtObjectInternal):
    link = prop(0x30, AllocLink)

class LinkedCOITracker(CTracker):
    item_cls = LinkedCOI
    sizeof_star = 0x238d8

class UndodogSub(GuestStruct):
    to_create = prop(0, fixed_array(LinkedCOITracker, 2))
    to_delete = prop(0x471b0, fixed_array(LinkedCOITracker, 2))
    point = prop(0x941a0, Point2D)
    # ...
    bytes = prop(0x941bc, fixed_array(u8, 16))
    sizeof_star = 0x941d0

class Undodog(GuestStruct):
    subs = prop(0x18, fixed_array(UndodogSub, 21))
    cur_sub_idx = prop(0xc70730, s32)
    @staticmethod
    def get(guest):
        return Undodog(guest, guest.read32(guest.slide_data(0x10195AEC)))

class KulerClassification(GuestStruct):
    idee = prop(0, u32)
    group = prop(4, u32)
    sizeof_star = 8
class Kuler(GuestStruct):
    classifications = prop(0x18, fixed_array(KulerClassification, 76))
    @staticmethod
    def get(guest):
        return Kuler(guest, guest.read32(guest.slide_data(0x101950DC)))
def main():
    real_guest = MemrwGuest.with_hostport(sys.argv[1])
    guest = CachingGuest(real_guest)
    vt_player = guest.slide_data(0x100CE7B8)

    print('text_slide=%#x data_slide=%#x' % (guest.text_slide, guest.data_slide))

    def print_yatsu_counts():
        by_objrec = {}
        with guest:
            for yatsu in mpobj.mp5.pointers.get_all():
                if yatsu:
                    by_objrec.setdefault(yatsu.objrec, []).append(yatsu)
            for objrec, yatsus in sorted(by_objrec.items()):
                print('%s: %d' % (objrec.get_name(), len(yatsus)))

    with guest:
        if 0:
            for i in range(0xee):
                _objrec = ObjRec.by_idee(i, guest)
                #print(i, hex(i), _objrec.name, hex(_objrec.vt), hex(_objrec.ctor))
                print('_%x_%s = 0x%x,' % (i, _objrec.name.as_str(), i))
            return
        if 0:
            brickish_edit_types = guest.slide_data(0x101D227C)
            for i in range(21):
                idee = guest.read32(brickish_edit_types + 4 * i)
                print('%#x: %s' % (i, ObjRec.by_idee(idee, guest).name))
            return


        mpobj = MakesPlayerObj.get(guest)
        player = None
        for obj in mpobj.tracker3.iter_allocs():
            #print(obj); continue

            vt = guest.read32(obj.addr + 0xb4)
            if vt == vt_player:
                player = obj.cast(Player)
                break

    if 0:
        im = guest.read(0x4ccfbd00, 1280*720*4) 
        open('/tmp/im.rgba', 'wb').write(im)
        return

    if 1 and sys.argv[2] == 'heaps':
        with guest:
            for heap in mpobj.heaps:
                print(f'{heap}: {heap.name.cstr} elm_size={heap.elm_size:#x} #free={heap.free_size / heap.elm_size}')

    if 0:
        with guest:
            for yatsu in mpobj.mp5.pointers.get_all():
                if not yatsu: continue
                #if yatsu.objrec.idee == 0x2e: # 0x32: # Player
                #    #yatsu.cast(PYES).loc.x = 3292.105957
                #    yatsu.cast(PYES).loc.x -= 5
                #    print(yatsu.cast(PYES).loc.y)
                # x=3350.86 is near-optimal
                # for y:
                # 292 bad
                # 292.3 ok
                #if yatsu.objrec.idee == 0x23: # Dossun
                #    yatsu.cast(PYES).loc.x = 3350.86
                #    yatsu.cast(PYES).loc.y = 292.3

                #if yatsu.objrec.idee == 0x2e: # giant BlackPakkun
                #    yatsu.cast(PYES).loc.x = 3304+8+7
                #if yatsu.objrec.idee == 0x23: # Dossun
                #    yatsu.cast(PYES).loc.y -= 0.1
                #if yatsu.objrec.idee == 0x1d: # PowBlock
                #    yatsu.cast(PYES).loc.x += 100
        return

    if 0:
        StateMgr(guest, 0x2c3445a0+0x10).print_cbs()
    if 1 and sys.argv[2] == 'ent':
        with guest:
            #print_yatsu_counts()
            #print(mpobj.mp5.pointers.base)
            for yatsu in mpobj.mp5.pointers.get_all():
                if yatsu and (
                    #yatsu.objrec.idee in {0x2d, 0x2e} or # BlackPakkun
                    #yatsu.objrec.idee == 0x1b or # JumpStepSide
                    #yatsu.objrec.idee == 0x20 # Met
                    #yatsu.objrec.idee == 0x1c # PSwitch
                    # yatsu.objrec.idee == 0x14 # HatenaBlock
                    #yatsu.objrec.idee == 0x22 # Dossun
                    #yatsu.objrec.idee == 0x48 # FirePakkun
                    True
                ):
                    #dump(yatsu)
                    #print(yatsu.vtable)
                    name = yatsu.objrec.get_name()
                    if name.startswith('Edit'):
                        yatsu = yatsu.cast(EditEntity)
                        loc_str = '%f,%f – %f,%f' % (yatsu.rect.min.x, yatsu.rect.min.y, yatsu.rect.max.x, yatsu.rect.max.y)
                        eapd_str = ' eapd=%s' % (yatsu.eapd,)
                    else:
                        yatsu = yatsu.cast(PYES)
                        loc_str = '%f,%f' % (yatsu.loc.x, yatsu.loc.y)
                        eapd_str = ''
                    print('%s @ %s %s %#x%s' % (name, loc_str, yatsu, yatsu.idbits, eapd_str))
                    #print(yatsu.loc)


    if 1 and sys.argv[2] == 'eapd':
        with guest:
            scl = SomeCoiList.get_ptr(guest).get()
            for sub1 in scl.sub1s[:1]:
                tracker = sub1.tracker
                print(tracker)
                for eapd in tracker.iter_allocs_rev():
                    eapd = eapd.cast(EditActorPlacementData)
                    #if eapd.my_type != 0xc: continue
                    objrec = ObjRec.by_idee(exported_type_to_idee(eapd.my_type, guest), guest)
                    name = objrec.get_name()
                    print(f'({eapd.x}, {eapd.y}): {name} mt={eapd.my_type:x} k={eapd.category} ct={eapd.child_type:x} f={eapd.my_flags:x} {eapd} eent={eapd.edit_entity}')
                    if eapd.x == 3096 and eapd.y == 24:
                        eapd.y += 16
    if 1 and sys.argv[2] == 'count':
        with guest:
            scl = SomeCoiList.get_ptr(guest).get()
            scl.sub1s[0].counts[0] = 50
            scl.sub1s[0].counts[1] = 50
            for i, sub1 in enumerate(scl.sub1s):
                print(f'world {i} counts: ({sub1.counts})')
                for j, count in enumerate(sub1.counts.get_all()):
                    print(f'   {j}: {count}')
    if 0:
        with guest:
            spawner = Spawner.get(guest)
            for j, count in enumerate(spawner.counts.get_all()):
                print(f'   {j}: {count}')
            #spawner.counts[0] += 80
            #print(spawner)

    if 0:
        with guest:
            mps = MP2.get_ptr(guest).get().sub
            for buck in mps.iter_allocs():
                objrec = ObjRec.by_idee(buck.idee, guest)
                spawn_rect = objrec.spawn_rect
                center = buck.x + spawn_rect.x
                halfwidth = spawn_rect.halfwidth
                left, right = center - halfwidth, center + halfwidth
                print(f'{buck.x}, {buck.y} ({buck.f_c}, {buck.f_10}) idee=0x{buck.idee:x}:{objrec.name.as_str()} l/r=({left}, {right})')
                #print(f'20={buck.f20:x} 24={buck.f24:x} flags:{buck.flags:x} {buck}')
    if 0:
        print(player.loc.x)
        #player.loc.x = 0
        #player.old_loc.x = 0
        #player.another_loc.x = 0
        print(player)

    if 0:
        guest.write32(guest.slide_text(0x022152CC), 0x48000048) # patch to enable unlimited selection

    if 1 and sys.argv[2] == 'zoom':
        zoom = float(sys.argv[3])
        if zoom != 1:
            guest.write32(guest.slide_text(0x023BC0D0), 0x7c031800) # patch to avoid crash in non-SMBU themes (changes rendering)
        coursething = CourseThing.get_ptr(guest).get()
        #print(addrof(coursething, 'camera_rect'))
        coursething.zoom = zoom
        if False and zoom == 1:
            time.sleep(0.5)
            guest.write32(guest.slide_text(0x023BC0D0), 0x2c030000) # undo previous patch

    if sys.argv[2] == 'cherry':
        # allow weird objects to come out of pipes/blasters:
        guest.write32(guest.slide_text(0x02636570), 0x38600001)
        guest.write32(guest.slide_text(0x0263652C), 0x38600001)
        guest.write32(guest.slide_text(0x0209E118), 0x60000000)
        # change 1up mushroom to point to Player:
        for i in range(4):
            guest.write32(guest.slide_data(0x1019ffcc + 4 * i), 0x32)
        # 'fixes' crash due to spawned Marios' invisible shellmets
        # guest.write32(guest.slide_text(0x026C40E0), 0x7c031800)
        guest.write32(guest.slide_text(0x026AA9A0), 0x60000000) # better patch that just disables that
        # 0x2838CD8 -> 3b000032
        print('ok')

    if 0:
        dx = 1500
        coursething = CourseThing.get_ptr(guest).get()
        player.loc.x += dx
        player.old_loc.x += dx
        player.another_loc.x += dx
        coursething.center.x += dx
        coursething.camera_rect.min.x += dx
        coursething.camera_rect.max.x += dx
        coursething.alt_left += dx
        coursething.alt_right += dx
    if 0:
        StrBinaryTree(guest, 0x107190d8).print()
    if 0:
        spawn_radius = f32(guest, guest.slide_data(0x1000FFB0))
        print(spawn_radius.get())
        spawn_radius.set(47)
        #spawn_radius.set(-50)
    if 0:
        with guest:
            for tracker_idx, tracker in enumerate(GroundsTrackers.get_ptr(guest).get().trackers):
                print(f'tracker {tracker_idx} ({tracker}): count={tracker.obj_count}')
                for obj in tracker.iter_allocs():
                    print(obj)

    if 0:
        with guest:
            undodog = Undodog.get(guest)
            print(undodog)
            print(f'cur_sub_idx = {undodog.cur_sub_idx}')
            #for idx_off in range(20, -1, -1):
            for idx_off in range(21):
                idx = (undodog.cur_sub_idx + idx_off + 1) % 21
                print(f'sub[{idx}]:')
                sub = undodog.subs[idx]
                print(f'bytes: {sub.bytes.get_all()}')

                for prop in ['to_create', 'to_delete']:
                    for idx in range(2):
                        tracker = getattr(sub, prop)[idx]
                        print(f'  {prop}[{idx}]: tracker={tracker} count={tracker.count}')
                        for linked_coi in tracker.iter_allocs():
                            print(f'    {linked_coi.desc()}')

    if 0:
        for i in range(70):
            idee = exported_type_to_idee(i, guest)
            objrec = ObjRec.by_idee(idee, guest)
            print(f'{i:x} -> {idee:x} -> {objrec.name}')

    if 0:
        kuler = Kuler.get(guest)
        for i, c in enumerate(kuler.classifications):
            idee = c.idee
            try:
                name = ObjRec.by_idee(idee, guest).name
            except IndexError:
                name = f'idee?{idee:#x}'
            print(f'{c.group}: {name}')
    return

    #dump(sys.stdout, mpobj.tracker)
    #dump(sys.stdout, mpobj.tracker2)
    #dump(sys.stdout, mpobj.tracker3)
    #dump(sys.stdout, player)
    #print(addrof(player.killer.statemgr, 'state'))
    #StateMgr(guest, 0x2bc7fc48+0x1d84).print_cbs()
    #return
    #player.killer.statemgr.print_cbs()
    if 0:
        while 1:
            for mgr in [player.statemgr_main, player.statemgr_demo, player.statemgr_mantanim, player.killer.statemgr]:
                state = mgr.state
                print(state, '<none>' if state == 0xffffffff else mgr.state_list[state].name, end=' ')
            print()
            #player.statemgr_demo.state = 5
            #print(addrof(player, 'min_y'))
            #print(player, hex(player.flags_430), hex(player.flags_434), player.y, player.min_y)
            time.sleep(0.05)

    #link = ObjLink(guest, guest.read32(slide_data(0x1036C1B0)))
    #while link:
    #    print(hex(link.addr), hex(link.objrec.addr), hex(link.free))
    #    #dump(sys.stdout, link.objrec)
    #    link = link.next

if __name__ == '__main__':
    main()
