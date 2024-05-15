import struct

class Point2D(GuestStruct):
    x = prop(0, f32)
    y = prop(4, f32)
    sizeof_star = 8
    def xy(self):
        return (self.x, self.y)
    full_repr = True
class Point3D(Point2D):
    z = prop(8, f32)
    sizeof_star = 12
    full_repr = True

class Size2D(GuestStruct):
    w = prop(0, f32)
    h = prop(4, f32)
    sizeof_star = 8
    full_repr = True

class Rect(GuestStruct):
    min = prop(0, Point2D)
    max = prop(8, Point2D)
    sizeof_star = 0x10
    def size(self):
        return (self.max.x - self.min.x, self.max.y - self.min.y)
    full_repr = True

class IntPoint2D(GuestStruct):
    x = prop(0, u32)
    y = prop(4, u32)
    sizeof_star = 8
    def xy(self):
        return (self.x, self.y)
    full_repr = True

class IntSize2D(GuestStruct):
    w = prop(0, u32)
    h = prop(4, u32)
    sizeof_star = 8
    full_repr = True
    def wh(self):
        return (self.w, self.h)

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
    prev = prop(0, lambda: ptr_to(SeadListNode))
    next = prop(8, lambda: ptr_to(SeadListNode))

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
        objrecs_by_idee = fixed_array(ptr_to(ObjRec), 0xee)(mm.addr.idee_to_objrec)
        return objrecs_by_idee[idee]

class Relly(GuestStruct):
    # afaik this is what I used to call EditActorPlacementData, but I'm
    # not sure that name is right
    idbits = prop(0xf0, u64)
    bg_unit_group = prop(0xf8, lambda: ptr_to(BgUnitGroup))
    x = prop(8, f32)
    y = prop(0xc, f32)
    z = prop(0x118, f32)
    x2 = prop(0x11c, f32)
    y2 = prop(0x120, f32)
    z2 = prop(0x124, f32)

class ActorBase(GuestStruct):
    vtable = prop(0, GuestPtrPtr)
    idbits = prop(0x30, s32)
    objrec = prop(0x38, lambda: ptr_to(ObjRec))
    world = prop(0x48, lambda: ptr_to(World)) # ?

class EditActor(ActorBase):
    relly = prop(0x320, ptr_to(Relly))

class Actor(ActorBase):
    if mm.version >= 300:
        loc = prop(0x230, Point3D)
    else:
        loc = prop(0x228, Point3D)
        houvelo = prop(0x234, Point3D)
        rngthing = prop(0x264, u32)
        source_xvel = prop(0x26c, f32)
        source_xvel_goal = prop(0x270, f32)
        gravity = prop(0x278, f32)
        source_xvel_step = prop(0x27c, f32)

class ScolNode(GuestStruct):
    self = prop(0, lambda: ptr_to(Scol))
    unk = prop(8, GuestPtrPtr)
    node = prop(0x10, SeadListNode)
    sizeof_star = 0x20

class ScolMid(GuestStruct):
    self = prop(0, lambda: ptr_to(Scol))
    unk = prop(8, GuestPtrPtr)
    list = prop(0x10, lambda: sead_list(ScolMidNode))
    sizeof_star = 0x28

class ScolSublist(GuestStruct):
    vt = prop(0, GuestPtrPtr)
    list = prop(8, lambda: sead_list(ColliderItem))
    sizeof_star = 0x20

class ScolSee(GuestStruct):
    f0 = prop(0, u32)
    f4 = prop(4, u32)
    f8 = prop(8, u32)
    sizeof_star = 0xc

class ScolResult(GuestStruct):
    valid = prop(0, u8)
    f1 = prop(1, u8)
    f2 = prop(2, u8)
    f3 = prop(3, u8)
    f4 = prop(4, u32)
    f8 = prop(8, u32)
    point1 = prop(0xc, Point2D)
    point2 = prop(0x14, Point2D)
    f1c = prop(0x1c, u32)
    actor_idbits = prop(0x20, u64)
    collider = prop(0x28, lambda: ptr_to(Collider))
    block_info = prop(0x30, u32)
    f34 = prop(0x34, u32)

    sizeof_star = 0x38

class ScolResultList(fixed_array(ScolResult, 7)):
    def should_dump_ith(self, i):
        return self[i].valid

class Scol(GuestStruct):
    vt = prop(0, GuestPtrPtr)
    h60 = prop(0x28, lambda: ColliderItemOuter)
    nodes = prop(0x98, fixed_array(ScolNode, 3))
    mid = prop(0xf8, ScolMid)
    sublist_120 = prop(0x120, ScolSublist) # contains Collider h60_2
    sublist_140 = prop(0x140, ScolSublist) # contains Collider h60_4
    sublist_160 = prop(0x160, ScolSublist) # contains Collider h60_3
    should_check_list1_mask = prop(0x180, u32)
    should_tick_38s = prop(0x184, u32)
    owner = prop(0x188, ptr_to(Actor))
    owners_pos_cur = prop(0x190, Point2D)
    owners_pos_old = prop(0x198, Point2D)
    owners_relatedtopos = prop(0x1a0, u8)

    point3d_ptrs = prop(0x1a8, fixed_array(ptr_to(Point3D), 8))
    point3d_storage = prop(0x1e8, fixed_array(Point3D, 8), dump=False)
    point3d_ptrs_by_point_idx = prop(0x248, fixed_array(ptr_to(Point3D), 4))
    point3ds_by_point_idx_storage = prop(0x268, fixed_array(Point3D, 4), dump=False)
    owner_idbits = prop(0x298, u64)

    ptr_owners_pos_cur = prop(0x2a0, ptr_to(Point2D))
    ptr_owners_pos_old = prop(0x2a8, ptr_to(Point2D))

    point_2b0 = prop(0x2b0, Point2D)

    flags_2b8 = prop(0x2b8, u32)
    flags_2bc = prop(0x2bc, u32)
    flags_2c0 = prop(0x2c0, u32)
    flags_2c4 = prop(0x2c4, u32)

    rects = prop(0x2c8, fixed_array(Rect, 4))
    point308 = prop(0x308, Point2D)
    point310 = prop(0x310, Point2D)
    point318 = prop(0x318, Point2D)
    point326 = prop(0x326, u16)

    something_from_collider = prop(0x328, u32)

    # these arrays are indexed by point index
    sees_360 = prop(0x360, fixed_array(ScolSee, 4))
    sees_valid = prop(0x390, fixed_array(u8, 4))
    sees_394 = prop(0x394, fixed_array(ScolSee, 4))
    result_list_idxs_cur = prop(0x3c4, fixed_array(u32, 4))
    result_list_idxs_old = prop(0x3d4, fixed_array(u32, 4))

    result_lists_cur = prop(0x3e8, fixed_array(ScolResultList, 4))
    result_lists_old = prop(0xa08, fixed_array(ScolResultList, 4))


class PlayerScolWrap(GuestStruct):
    scol = prop(0x10, Scol)
class Player(Actor):
    scol_wrap = prop(0x12a0, PlayerScolWrap)

class LiftSegmentIsh(GuestStruct):
    smgr = prop(0xd0, StateMgr)

class YouganLift(Actor):
    part_statemgrs = prop(0x2dd8, fixed_array(ptr_to(LiftSegmentIsh), 4))

class MP5(GuestStruct):
    pointers = prop(0x0, count4_ptr(ptr_to(ActorBase)))

class RNG(GuestStruct):
    state = fixed_array(u32, 4)

class RNGPlus(GuestStruct):
    rng = prop(8, RNG)

class Spawner(GuestStruct):
    counts = prop(8, fixed_array(u32, 8)) # not sure about length

class ColliderSegment(GuestStruct):
    which_side = prop(0, u8)
    rel_pos_1 = prop(4, Point2D)
    rel_pos_2 = prop(0xc, Point2D)
    last_word = prop(0x14, u32)
    sizeof_star = 0x18

class ScolMidNode(GuestStruct):
    node = prop(0, SeadListNode)
    owner = prop(0x10, lambda: ptr_to(Collider))
    scol_mid = prop(0x18, lambda: ptr_to(ScolMid))

class Collider(GuestStruct):
    # aka HasBlockInfo
    vtable = prop(0, GuestPtrPtr)
    scol_mid_node = prop(0x20, ScolMidNode, dump=False)
    items = prop(0x58, lambda: fixed_array(ColliderItemOuter, 5))
    bbox_cur = prop(0x238, Rect)
    bbox_old = prop(0x248, Rect)
    bbox_both = prop(0x258, Rect)
    field_26a = prop(0x26a, u8)
    flags_270 = prop(0x270, u32)
    actor = prop(0x278, ptr_to(Actor))
    actor_idbits = prop(0x280, u32)
    block_owner = prop(0x288, GuestPtrPtr)
    ext_pos_cur = prop(0x290, ptr_to(Point2D))
    ext_pos_old = prop(0x298, ptr_to(Point2D))
    ext_unk = prop(0x2a0, ptr_to(u32), dump_deep=True)
    int_off_cur = prop(0x2b0, Point2D)
    int_off_old = prop(0x2b8, Point2D)
    int_aoff_cur = prop(0x2c0, Point2D)
    int_aoff_old = prop(0x2c8, Point2D)
    int_boff_cur = prop(0x2d0, Point2D)
    int_boff_old = prop(0x2d8, Point2D)
    some_bitmask = prop(0x2e0, u32) # & 1 means no collision, used for coins
    base_block_info = prop(0x364, u32)
    ext_size = prop(0x3b8, ptr_to(fixed_array(f32, 4)), dump_deep=True)
    segments_cur = prop(0x3c0, count4_ptr(ColliderSegment))
    segments_old = prop(0x3d0, count4_ptr(ColliderSegment))

class BlockColliderOwner(GuestStruct):
    # aka HBO1
    collider = prop(0x38, Collider)

class Bloch(GuestStruct):
    block_collider_owners = prop(0x18, count4_ptr(ptr_to(BlockColliderOwner)))

class ColliderItem(GuestStruct):
    # aka h60 + 0x20
    # XXX naming!
    node = prop(0, SeadListNode)
    node_ptr = prop(0x10, GuestPtrPtr)
    list = prop(0x18, GuestPtrPtr) # todo: check
    vtable = prop(0x20, GuestPtrPtr)
    owner = prop(0x28, ptr_to(Collider)) # could be Collider or not
    callback = prop(0x30, GuestPtrPtr)

class ColliderItemOuter(GuestStruct):
    # aka H60
    item = prop(0x20, ColliderItem)
    sizeof_star = 0x60

class BlockColliderListItem(GuestStruct):
    # aka 24k_item
    collider = prop(0x8, ptr_to(Collider), dump_deep=True)

class BlockColliderListEntry(GuestStruct):
    item = prop(0x10, ptr_to(BlockColliderListItem), dump_deep=True)

class BGCollisionGridSquare(GuestStruct):
    # aka triple_24klist
    list0 = prop(0x00, sead_list(BlockColliderListEntry))
    list1 = prop(0x18, sead_list(BlockColliderListEntry))
    list2 = prop(0x30, sead_list(BlockColliderListEntry))
    sizeof_star = 0x48
    def any_nonempty(self):
        return self.list0.count or self.list1.count or self.list2.count

class BGCollisionGrid(GuestStruct, GuestArray):
    # Grid coordinates are in units of blocks.

    # (0, 0) corresponds to (x, y) in the grid; thus some negative coordinates
    # are in the grid.
    base_pos = prop(0, IntPoint2D)

    size_if_horizontal = prop(8, IntSize2D)
    size_if_vertical = prop(0x10, IntSize2D)
    count = prop(0x18, u32)
    base = prop(0x20, ptr_to(BGCollisionGridSquare))
    ptr_ty = BGCollisionGridSquare
    is_vertical_level = prop(0x28, u8)

    def size(self):
        if self.is_vertical_level:
            return self.size_if_horizontal
        else:
            return self.size_if_vertical

    def coord_range(self):
        width, height = self.size().wh()
        base_x, base_y = self.base_pos.xy()
        x_bounds = (-base_x, width - base_x)
        y_bounds = (-base_y, height - base_y)
        return (x_bounds, y_bounds)

    def square(self, x, y):
        x_bounds, y_bounds = self.coord_range()
        if (not (x_bounds[0] <= x < x_bounds[1]) or
            not (y_bounds[0] <= y < y_bounds[1])):
            return None
        width = self.size().w
        return self[(y - y_bounds[0]) * width + (x - x_bounds[0])]

    def squares(self):
        width = self.size().w
        base_x, base_y = self.base_pos.xy()
        with guest:
            self.cache_all()
            for i in range(self.count):
                yield ((i % width) - base_x, (i // width) - base_y, self[i])

    def nonempty_squares(self):
        for (x, y, square) in self.squares():
            if square.any_nonempty() != 0:
                yield (x, y, square)

    def dump(self, fp, indent, **opts):
        super().dump(fp, indent, **opts)
        indent2 = indent + '  '
        fp.write(f'\n{indent2}nonempty squares:')
        indent3 = indent2 + '  '
        for x, y, square in self.nonempty_squares():
            fp.write(f'\n{indent3}({x}, {y}): ')
            square.dump(fp, indent3, **opts)

class BGCollisionSystem(GuestStruct):
    # things that can land
    colliders1 = prop(0x38, sead_list(ColliderItem))

    # things that can be landed on (not blocks)
    colliders2 = prop(0x58, sead_list(ColliderItem))

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
    full_repr = True

class Tiler2Grid(GuestStruct, GuestArray):
    width = prop(0, u32)
    height = prop(4, u32)
    base = prop(8, ptr_to(GridSquare))
    ptr_ty = GridSquare

    @property
    def count(self):
        return self.width * self.height

    def _squares(self):
        width = self.width
        return ((i % width, i // width, self[i])
                for i in range(self.count))

    def nonempty_squares(self):
        with guest:
            self.cache_all()
            return [(x, y, square)
                    for (x, y, square) in self._squares()
                    if square.what_to_draw != 0]

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
    name = prop(8, FancyString)
    id = prop(0x20, u32)
    actor_mgr = prop(0x18, lambda: ptr_to(ActorMgr))
    area_sys = prop(0x140, ptr_to(AreaSystem)) # was 0x130

class ActorMgr(GuestStruct):
    @staticmethod
    def get():
        return guest_read_ptr(ActorMgr, mm.addr.actor_mgr)
    mp5 = prop(0x30, ptr_to(MP5))
    cur_world = prop(0x98, ptr_to(World))
    worlds = prop(0x80, count4_ptr(ptr_to(World)))

class OtherTimerRelated(GuestStruct):
    @staticmethod
    def get():
        return guest_read_ptr(OtherTimerRelated, mm.addr.other_timer_related)
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
        return guest_read_ptr(CoinMan, mm.addr.coinman)

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
    unit_group_heaps = prop(0x58, fixed_array(GuestPtrPtr, 2))
    @staticmethod
    def get():
        return guest_read_ptr(BgUnitGroupMgr, mm.addr.bg_unit_group_mgr)

def block_kind_info_array():
    return fixed_array(BlockKindInfo, 0x1e)(mm.addr.block_kind_info_array)

def commandlike(f):
    f.commandlike = True
    return f

@commandlike
def print_exported_types():
    for i in range(70):
        idee = exported_type_to_idee(i)
        objrec = ObjRec.by_idee(idee)
        print(f'{i:x} -> {idee:x} -> {objrec.name}')

@commandlike
def print_idees():
    for idee in range(0xee):
        objrec = ObjRec.by_idee(idee)
        print(f'{idee:x} -> {objrec.base_name},{objrec.variation_name} {objrec.get_name()} or={objrec}')

@commandlike
def print_ent():
   for yatsu in ActorMgr.get().mp5.pointers.get_all():
        if yatsu and (
            True
        ):
            name = yatsu.objrec.get_name()
            if name.startswith('Edit'):
                yatsu = yatsu.cast(EditActor)
                relly = yatsu.relly
                loc_str = '%f,%f' % (relly.x, relly.y)
            else:
                yatsu = yatsu.cast(Actor)
                loc_str = '%f,%f' % (yatsu.loc.x, yatsu.loc.y)
            print(f'{name} @ {loc_str} {yatsu} {yatsu.idbits:#x}')

@commandlike
def print_timer():
    print(OtherTimerRelated.get().frames)

@commandlike
def print_block_kind_info():
    bkia = block_kind_info_array()
    for i, bki in enumerate(bkia):
        print(f'0x{i:02}: bits=0x{bki.bits:8} {bki.name}')

def _print_collider(collider):
    print(f'  {collider}')
    actor = collider.actor
    block_owner = collider.block_owner
    if actor:
        print(f'    actor:{actor.addr:#x} {actor.objrec.get_name()}')
    if block_owner:
        print(f'    block_owner:{block_owner.addr:#x}')
    for kind in ('cur', 'old'):
        pos = getattr(collider, f'ext_pos_{kind}')
        int_off = getattr(collider, f'int_off_{kind}')
        int_aoff = getattr(collider, f'int_aoff_{kind}')
        int_boff = getattr(collider, f'int_boff_{kind}')
        print(f'    {kind}:   pos={pos.xy()}   off={int_off.xy()}  aoff={int_aoff.xy()}  boff={int_boff.xy()}')
        bbox = getattr(collider, f'bbox_{kind}')
        width, height = bbox.size()
        print(f'           bbox: x:{bbox.min.x}-{bbox.max.x} y:{bbox.min.y}-{bbox.max.y} size:{width},{height}')
        segments = getattr(collider, f'segments_{kind}')
        for i, seg in enumerate(segments):
            print(f'           segments[{i}]: x:{seg.rel_pos_1.x:5} - {seg.rel_pos_2.x:5} y:{seg.rel_pos_1.y:5} - {seg.rel_pos_2.y:5} which={seg.which_side} last={seg.last_word:#x}')
    bbox = collider.bbox_both
    width, height = bbox.size()
    print(f'    bbox_both: x:{bbox.min.x}-{bbox.max.x} y:{bbox.min.y}-{bbox.max.y} size:{width},{height}')
    print(f'    ext_unk:   {collider.ext_unk.get():#08x}        bm:{collider.some_bitmask:#x} f:{collider.flags_270:#x}')
    ext_size = ' '.join(str(collider.ext_size[i]) for i in range(4))
    print(f'    ext_size: {ext_size}   info:0x{collider.base_block_info:8x}')

@commandlike
def print_bg():
    world = ActorMgr.get().cur_world
    array = world.area_sys.bloch.block_collider_owners
    print(f'world {world.id}: ({array.count})')
    for bco in array:
        _print_collider(bco.collider)

@commandlike
def print_grid():
    seen = set()
    for x, y, square in ActorMgr.get().cur_world.area_sys.bg_collision_system.grid.squares():
        for slist in [square.list0, square.list1, square.list2]:
            for entry in slist:
                collider = entry.item.collider
                if collider in seen:
                    continue
                seen.add(collider)
                _print_collider(collider)
