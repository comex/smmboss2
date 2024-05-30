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

class TT2(GuestStruct):
    cb_direct = prop(0xc, u32)
    sizeof_star = 0x10

class TransThing(GuestStruct):
    tt2_cur = fixed_array(ptr_to(TT2), 10)
    tt2_avail = fixed_array(ptr_to(TT2), 47)
    sizeof_star = 0xe4

@functools.lru_cache(None)
def exported_type_to_idee_TODO(n, guest):
    array = fixed_array(u32, 70)(guest, guest.slide_data(0x103354FC))
    return array[n]



class MP5(GuestStruct):
    pointers = prop(0, count_ptr(ptr_to(Entity)))

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
