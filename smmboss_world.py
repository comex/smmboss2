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

class StateMgr(GuestStruct):
    counter = prop(0xc, u32)
    state = prop(0x8, u32)
    names_count = prop(0x38, u32)
    names = prop(0x40, ptr_to(fixed_array(FancyString, 999)))

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

class AreaSystem(GuestStruct):
    rngplus = prop(0xf8, ptr_to(RNGPlus))
    spawner = prop(0x70, ptr_to(Spawner))

class World(GuestStruct):
    actor_mgr = prop(0x18, lambda: ptr_to(ActorMgr))
    area_sys = prop(0x130, ptr_to(AreaSystem))

class ActorMgr(GuestStruct):
    @staticmethod
    def get():
        return guest.read_ptr(ActorMgr, guest.addr.actor_mgr)
    mp5 = prop(0x30, ptr_to(MP5))
    cur_world = prop(0x98, ptr_to(World))

class OtherTimerRelated(GuestStruct):
    @staticmethod
    def get():
        return guest.read_ptr(OtherTimerRelated, guest.addr.other_timer_related)
    frames = prop(0x38, u32)

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
