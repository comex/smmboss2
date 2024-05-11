import struct

class Point2D(GuestStruct):
    x = prop(0, f32)
    y = prop(4, f32)
    sizeof_star = 8
    def xy(self):
        return (self.x, self.y)
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
    def size(self):
        return (self.max.x - self.min.x, self.max.y - self.min.y)

class IntPoint2D(GuestStruct):
    x = prop(0, u32)
    y = prop(4, u32)
    sizeof_star = 8
    def xy(self):
        return (self.x, self.y)

class IntRect(GuestStruct):
    min = prop(0, IntPoint2D)
    max = prop(8, IntPoint2D)
    sizeof_star = 0x10
    def size(self):
        return (self.max.x - self.min.x, self.max.y - self.min.y)

class SpawnRect(GuestStruct):
    x = prop(0x00, u32)
    y = prop(0x04, u32)
    halfwidth = prop(0x08, u32)
    halfheight = prop(0x0c, u32)
    bits = prop(0x10, u16)

class FancyString(GuestStruct):
    vtable = prop(0, usize)
    cstr = prop(8, ptr_to(GuestCString))
    sizeof_star = 0x10
    def __repr__(self):
        return repr(self.cstr)
    def as_str(self):
        return self.cstr.as_str()

class SeadListNode(GuestStruct):
    prev = prop(0, ptr_to(lambda: SeadListNode))
    next = prop(8, ptr_to(lambda: SeadListNode))

class SeadListImpl(SeadListNode):
    count = prop(0x10, u32)
    link_offset = prop(0x14, u32)
    sizeof_star = 0x18

    def __iter__(self, rev=False):
        expected_count = self.count
        actual_count = 0
        link_offset = self.link_offset
        link = self.prev if rev else self.next
        while link != self:
            next = link.prev if rev else link.next
            yield link.raw_offset(-link_offset, self.elem_ty)
            link = next
            actual_count += 1
            assert actual_count <= expected_count
        assert actual_count == expected_count

    def __getitem__(self, i):
        count = self.count
        if i < 0:
            i += count
        assert 0 <= i < count, (i, count)
        it = iter(self)
        for j in range(i):
            next(it)
        return next(it)

    def dump(self, fp, indent, **opts):
        fp.write(f'sead::List ({self.addr:#x}, count={self.count}):')
        i = 0
        indent2 = indent + '  '
        for item in self:
            fp.write(f'\n{indent2}[{i}] = ')
            dump(item, fp, indent2, **opts)
            fp.write(',')
            i += 1

def sead_list(_elem_ty):
    class C(SeadListImpl):
        elem_ty = _elem_ty
    C.__name__ = f'sead_list({_elem_ty.__name__})'
    return C


class StateMgrState(GuestStruct):
    sizeof_star = 0x40
    vtable = prop(0, usize)
    target = prop(8, GuestPtrPtr)
    cb_in = prop(0x10, GuestPtrToMemberFunction)
    cb_tick = prop(0x20, GuestPtrToMemberFunction)
    cb_out = prop(0x30, GuestPtrToMemberFunction)

class StateMgr(GuestStruct):
    counter = prop(0xc, u32)
    state = prop(0x8, u32)
    state_objs = prop(0x28, count4_ptr(StateMgrState))
    names = prop(0x38, count4_ptr(FancyString))

    def dump_states(self):
        count = self.state_objs.count
        assert count == self.names.count
        for i in range(count):
            state = self.state_objs[i]
            line = f'State {i}: name={self.names[i]}'
            for kind in ['in', 'tick', 'out']:
                cb = getattr(state, f'cb_{kind}')
                func = guest.unslide(cb.resolve(state.target).addr)
                line += f' {kind}={func:#x}'
            line += f' [target={state.target.addr:#x}]'
            print(line)

class ObjRec(GuestStruct):
    vt = prop(0, usize)
    ctor = prop(8, usize)
    idee = prop(0x10, u32)
    spawn_rect = prop(0x18, ptr_to(SpawnRect))
    base_name = prop(0x28, FancyString)
    variation_name = prop(0x38, FancyString)
    @functools.lru_cache(None)
    def get_name(self):
        name = self.variation_name.as_str() or self.base_name.as_str()
        return '%s(%x)' % (name, self.idee)
    @staticmethod
    @functools.lru_cache(None)
    def by_idee(idee):
        objrecs_by_idee = fixed_array(ptr_to(ObjRec), 0xee)(guest.addr.idee_to_objrec)
        return objrecs_by_idee[idee]

class Entity(GuestStruct):
    vtable = prop(0, GuestPtrPtr)
    idbits = prop(0x30, s32)
    objrec = prop(0x38, lambda: ptr_to(ObjRec))
    mama = prop(0x48, lambda: ptr_to(Entity))

class PYES(Entity):
    if guest.version >= 300:
        loc = prop(0x230, Point3D)
    else:
        loc = prop(0x228, Point3D)
        houvelo = prop(0x234, Point3D)
        rngthing = prop(0x264, u32)
        source_xvel = prop(0x26c, f32)
        source_xvel_goal = prop(0x270, f32)
        gravity = prop(0x278, f32)
        source_xvel_step = prop(0x27c, f32)

class LiftSegmentIsh(GuestStruct):
    smgr = prop(0xd0, StateMgr)

class YouganLift(PYES):
    part_statemgrs = prop(0x2dd8, fixed_array(ptr_to(LiftSegmentIsh), 4))

class MP5(GuestStruct):
    pointers = prop(0x0, count4_ptr(ptr_to(Entity)))

class RNG(GuestStruct):
    state = fixed_array(u32, 4)

class RNGPlus(GuestStruct):
    rng = prop(8, RNG)

class Spawner(GuestStruct):
    counts = prop(8, fixed_array(u32, 8)) # not sure about length

class BG(GuestStruct):
    rect = prop(0x238, fixed_array(Rect, 3))
    base_info = prop(0x364, u32)
    ext_pos = prop(0x290, fixed_array(ptr_to(Point2D), 2))
    ext_unk = prop(0x2a0, ptr_to(u32))
    int_off = prop(0x2b0, fixed_array(Point2D, 3))
    ext_size = prop(0x3b8, ptr_to(fixed_array(f32, 4)))

class NormalBgOuter(GuestStruct):
    # aka HBO1
    bg = prop(0x38, BG)

class Bloch(GuestStruct):
    normal_bg_outer_array = prop(0x18, count4_ptr(ptr_to(NormalBgOuter)))

class Collider(GuestStruct):
    # aka h60 + 0x20
    pass

class BGCollisionGrid(GuestStruct):
    baseidx = prop(0, IntPoint2D)
    first_wh = prop(8, IntPoint2D)
    second_wh = prop(0x10, IntPoint2D)
    triple_count = prop(0x18, u32)
    field_1c = prop(0x1c, u32)
    triple_array = prop(0x20, GuestPtrPtr)

class BGCollisionSystem(GuestStruct):
    colliders1 = prop(0x38, sead_list(Collider))
    colliders2 = prop(0x58, sead_list(Collider))
    grid = prop(0x18, ptr_to(BGCollisionGrid))

class Tile(GuestStruct):
    what_to_draw = prop(0, u32)
    field_4 = prop(4, u32)
    pos = prop(8, Point3D)
    sizeof_star = 0x14

class TileArray(GuestArray, GuestStruct):
    base = prop(0, ptr_to(Tile))
    capacity = prop(8, u32)
    offset = prop(0xc, u32) # ?
    count = prop(0x10, u32)
    ptr_ty = Tile

class GridSquare(GuestStruct):
    what_to_draw = prop(0, u16)
    field_2 = prop(2, u8)
    field_3 = prop(3, u8)
    sizeof_star = 4

    @staticmethod
    def decode_data(data):
        # fast path when getting all.  it's a hack.
        return struct.unpack('<HBB', data)

class Tiler2Grid(GuestStruct, GuestArray):
    width = prop(0, u32)
    height = prop(4, u32)
    base = prop(8, ptr_to(GridSquare))
    ptr_ty = GridSquare

    @property
    def count(self):
        return self.width * self.height

    def squares(self):
        width = self.width
        return [(i // width, i % width, decoded)
                for (i, decoded) in enumerate(self.get_all())]

    def nonzero_squares(self):
        zeroes = (0, 0, 0)
        return [(x, y, decoded)
                for (x, y, decoded) in self.squares()
                if decoded != zeroes]

    def square(self, x, y):
        assert 0 <= x < self.width
        assert 0 <= y < self.height
        return self[y * self.width + x]

class SparkleEntry(GuestStruct):
    pass

class SparkleTable(GuestStruct, GuestArray):
    count = prop(0, u32)
    capacity = prop(4, u32)
    base = prop(8, ptr_to(SparkleEntry))
    next_alloc = prop(0x10, ptr_to(SparkleEntry))
    storage = prop(0x18, GuestPtrPtr)
    ptr_ty = SparkleEntry

class SparkleTableOuter(GuestStruct):
    table = prop(8, SparkleTable)

class Tiler2(GuestStruct):
    grid1 = prop(0x08, Tiler2Grid)
    grid2 = prop(0x18, Tiler2Grid)
    grid3 = prop(0x28, Tiler2Grid)
    tiles = prop(0x48, TileArray)
    sparkle = prop(0x60, ptr_to(SparkleTableOuter))

class AreaSystem(GuestStruct):
    world_id = prop(0x18, u32)
    also_world_id = prop(0x1c, u32)
    use_second_coords = prop(0x30, u8)
    spawner = prop(0x70, ptr_to(Spawner))
    bg_collision_system = prop(0x90, ptr_to(BGCollisionSystem))
    bloch = prop(0xa0, ptr_to(Bloch))
    rngplus = prop(0xf8, ptr_to(RNGPlus))
    tiler2 = prop(0x110, ptr_to(Tiler2))

class World(GuestStruct):
    id = prop(0x20, u32)
    actor_mgr = prop(0x18, lambda: ptr_to(ActorMgr))
    area_sys = prop(0x140, ptr_to(AreaSystem)) # was 0x130

class ActorMgr(GuestStruct):
    @staticmethod
    def get():
        return guest.read_ptr(ActorMgr, guest.addr.actor_mgr)
    mp5 = prop(0x30, ptr_to(MP5))
    #cur_world = prop(0x98, ptr_to(World))
    worlds = prop(0x80, count4_ptr(ptr_to(World)))

class OtherTimerRelated(GuestStruct):
    @staticmethod
    def get():
        return guest.read_ptr(OtherTimerRelated, guest.addr.other_timer_related)
    frames = prop(0x38, u32)

class BlockKindInfo(GuestStruct):
    bits = prop(0, u32)
    name = prop(8, ptr_to(GuestCString))
    sizeof_star = 0x10

class CoinInfo(GuestStruct):
    x = prop(0, f32)
    y = prop(4, f32)
    world = prop(8, u32)
    sizeof_star = 0xc

class CoinInfoArray(GuestArray, GuestStruct):
    ptr_ty = CoinInfo
    base = prop(0, ptr_to(CoinInfo))
    capacity = prop(8, u32)
    unk_c = prop(0xc, u32)
    count = prop(0x10, u32)
    coin_info_storage = prop(0x14, fixed_array(CoinInfo, 10))

class PendingCoinInfo(GuestStruct):
    area_sys = prop(0x0, ptr_to(AreaSystem))
    mario_pos = prop(0x8, Point2D)
    coin_pos = prop(0x10, Point2D)
    f18 = prop(0x18, u32)
    f1c = prop(0x1c, u32)
    f20 = prop(0x20, u64)
    sizeof_star = 0x28

class PendingCoinInfoArray(GuestArray, GuestStruct):
    ptr_ty = PendingCoinInfo
    base = prop(0, ptr_to(PendingCoinInfo))
    capacity = prop(8, u32)
    unk_c = prop(0xc, u32)
    count = prop(0x10, u32)
    coin_info_storage = prop(0x14, fixed_array(PendingCoinInfo, 5), dump=False)
    sizeof_star = 0xe0

class CoinMan(GuestStruct):
    vt = prop(0, usize)
    max_coins = prop(0x20, u32)
    cur_info = prop(0x28, CoinInfoArray)
    also_max_coins = prop(0xb8, u32)
    saved_info = prop(0xc0, CoinInfoArray)
    pending = prop(0x150, fixed_array(PendingCoinInfoArray, 2))
    f310 = prop(0x310, u32)
    flag314 = prop(0x314, u8)
    @staticmethod
    def get():
        return guest.read_ptr(CoinMan, guest.addr.coinman)

class BgUnitGroupTypeSpecificVtable(GuestStruct):
    get_name = prop(0x50, GuestPtrPtr)

class BgUnitGroupTypeSpecific(GuestStruct):
    vt = prop(0, ptr_to(BgUnitGroupTypeSpecificVtable))
    @functools.cached_property
    def name(self):
        stuff = emulate_call(self.vt.get_name.addr, x0=self.addr)
        return stuff['guest'].read_cstr(stuff['ret'])

class BgUnitGroup(GuestStruct):
    node1 = prop(0x20, SeadListNode)
    node2 = prop(0x30, SeadListNode)
    type_specific = prop(0x40, ptr_to(BgUnitGroupTypeSpecific))
    heap = prop(0x48, GuestPtrPtr)
    type = prop(0x50, u32)
    rect = prop(0x54, IntRect) # in units of blocks
    field_64 = prop(0x64, u32)
    field_68 = prop(0x68, u32)
    pos = prop(0x6c, Point3D) # in native units
    scale = prop(0x78, Size2D)
    field_84 = prop(0x84, u32)
    field_88 = prop(0x88, u32)
    field_8c = prop(0x8c, u32)
    tilt = prop(0x90, Point2D)
    field_98 = prop(0x98, u32)
    field_9c = prop(0x9c, u32)
    field_a0 = prop(0xa0, u32)
    field_a4 = prop(0xa4, u32)
    field_a8 = prop(0xa8, u32)
    flags = prop(0xac, u32)
    field_b0 = prop(0xb0, u32)
    world_idx = prop(0xb4, u8)
    field_b8 = prop(0xb8, u32)
    field_bc = prop(0xbc, u32)

    def dump(self, fp, indent, **opts):
        super().dump(fp, indent, **opts)
        fp.write(f'\n{indent}  type_specific.name: {self.type_specific.name}')

class BgUnitGroupMgr(GuestStruct):
    unit_group_lists = prop(0x28, fixed_array(sead_list(BgUnitGroup), 2))
    unit_group_heaps = prop(0x58, fixed_array(GuestPtr, 2))
    @staticmethod
    def get():
        return guest.read_ptr(BgUnitGroupMgr, guest.addr.bg_unit_group_mgr)

def block_kind_info_array():
    return fixed_array(BlockKindInfo, 0x1e)(guest.addr.block_kind_info_array)

def print_exported_types():
    for i in range(70):
        idee = exported_type_to_idee(i)
        objrec = ObjRec.by_idee(idee)
        print(f'{i:x} -> {idee:x} -> {objrec.name}')

def print_idees():
    for idee in range(0xee):
        objrec = ObjRec.by_idee(idee)
        print(f'{idee:x} -> {objrec.base_name},{objrec.variation_name} {objrec.get_name()} or={objrec}')

def print_ent():
   for yatsu in ActorMgr.get().mp5.pointers.get_all():
        if yatsu and (
            True
        ):
            name = yatsu.objrec.get_name()
            if name.startswith('Edit'):
                pass # ...
                loc_str = '?'
            else:
                yatsu = yatsu.cast(PYES)
                loc_str = '%f,%f' % (yatsu.loc.x, yatsu.loc.y)
            print(f'{name} @ {loc_str} {yatsu} {yatsu.idbits:#x}')

def print_timer():
    print(OtherTimerRelated.get().frames)

def print_block_kind_info():
    bkia = block_kind_info_array()
    for i, bki in enumerate(bkia):
        print(f'0x{i:02}: bits=0x{bki.bits:8} {bki.name}')

def print_bg():
    for i, world in enumerate(ActorMgr.get().worlds):
        if (area_sys := world.area_sys):
            array = area_sys.bloch.normal_bg_outer_array
            print(f'world {i}: ({array.count})')
            for normal in array:
                print(f'  {normal}')
                for i in range(3):
                    rect = normal.bg.rect[i]
                    width, height = rect.size()
                    print(f'    rect{i}: x:{rect.min.x}-{rect.max.x} y:{rect.min.y}-{rect.max.y} size:{width},{height}')
                line = '    ext_pos'
                for i in range(2):
                    line += f'    {i}:{normal.bg.ext_pos[i].xy()}'
                print(line)
                print(f'    ext_unk:   {normal.bg.ext_unk.get()}')
                line = '    int_off'
                for i in range(3):
                    line += f'    {i}:{normal.bg.int_off[i].xy()}'
                print(line)
                print(f'    ext_size: ' + ' '.join(str(normal.bg.ext_size[i]) for i in range(4)))
