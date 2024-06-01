#include "lib.hpp"
#include "serve.hpp"
#include "stuff.hpp"
#include <stdarg.h>
#include <array>
#include <variant>

// TODO: move this

namespace nn::diag::detail {
    int PrintDebugString(const char *);
}

void log_str(const char *str) {
    nn::diag::detail::PrintDebugString(str);
}

__attribute__((format(printf, 1, 2)))
void xprintf(const char *fmt, ...) {
    char buf[196];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    log_str(buf);
}

enum mm_version {
    VER_301,
    VER_302,
    VER_COUNT,
};

static const mm_version s_cur_mm_version = VER_301; // XXX
static uintptr_t s_target_start;

static uintptr_t assert_nonzero(uintptr_t offset) {
    assert(offset);
    return offset;
}

// -- Naming
//
// All of these classes are things defined either in Mario Maker or in the SDK.
// The naming convention is not random.
//
// `CamelCase` is used for names that either definitely or probably match the
// actual name in the original source code.
//
// `snake_case` is used for names that are made up.
//
// These may be mixed: e.g., `AreaSystem_do_many_collisions` represents a
// method with an unknown name on a class with a (probably-)known name.
//
// Actual type and function names may be discovered in a few different ways:
// - The dynamically linked SDK has mostly full symbols.
// - Some version of Splatoon 2 had symbols for the main binary, and it
//   statically links many of the same libraries as Mario Maker 2.
// - Some names are revealed through strings (though these are more likely to
//   be guesses).
//
// -- Conventions
//
// Most of these types are defined in an unconventional way, using PROP macros
// instead of real fields.
//
// This has two purposes:
// - Allowing the offset of each field to be explicitly specified, without
//   needing to manually add padding for unknown regions of the type.
//
// - Making it easier to have one binary that supports multiple versions of
//   Mario Maker (which might have fields at different offsets).

union mm_pointer_to_member_function {
    struct {
        uint64_t _;
        uint64_t is_virt:1,
                 :63;
    };
    struct {
        uint64_t func;
        uint64_t zero;
    } nonvirt;
    struct {
        uint64_t vtable_offset;
        int64_t  is_virt:1,
                 offset_to_vtable:63;
    } virt;

    void *resolve(void *object) const {
        if (is_virt) {
            uintptr_t vtable = *(uintptr_t *)((char *)object + virt.offset_to_vtable);
            return *(void **)(vtable + virt.vtable_offset);
        } else {
            return (void *)nonvirt.func;
        }
    }
};
static_assert(sizeof(mm_pointer_to_member_function) == 0x10);

// sead::ListNode
struct mm_ListNode {
    mm_ListNode *prev;
    mm_ListNode *next;
};

// sead::ListImpl
struct mm_ListImpl {
    mm_ListNode head;
    uint32_t count;
    uint32_t offset_to_link;
};

// wrapper, no evidence for original
template <typename T>
struct mm_list_iterator {
    mm_ListNode *cur_node_;
    uint32_t offset_to_link_;
    mm_ListNode *end_;

    T &operator*() const {
        assert(cur_node_ != end_);
        return *(T *)((char *)cur_node_ - offset_to_link_);
    }
    mm_list_iterator &operator++() {
        assert(cur_node_ != end_);
        cur_node_ = cur_node_->next;
        return *this;
    }
    bool operator==(const mm_list_iterator &other) const {
        return cur_node_ == other.cur_node_;
    };
};

// wrapper, no evidence for original
template <typename T, bool use_offset_to_link = true>
struct mm_list : public mm_ListImpl {
    mm_list_iterator<T> begin() { return mm_list_iterator<T>{head.next, get_offset_to_link(), &head}; }
    mm_list_iterator<T> end()   { return mm_list_iterator<T>{&head, get_offset_to_link(), &head}; }
    uint32_t get_offset_to_link() {
        return use_offset_to_link ? offset_to_link : 0;
    }
};

template <typename self_t, typename T>
struct mm_count_ptr_methods {
    pt_pointer<T> begin() const { return ((self_t *)this)->ptr; }
    pt_pointer<T> end() const { return begin() + ((self_t *)this)->count; }
};

template <typename T>
struct mm_count_ptr : public mm_count_ptr_methods<mm_count_ptr<T>, T> {
    uint32_t count;
    pt_pointer<T> ptr;
};

template <typename T>
struct mm_count_cap_ptr : public mm_count_ptr_methods<mm_count_cap_ptr<T>, T>  {
    uint32_t count;
    uint32_t cap;
    pt_pointer<T> ptr;
};

struct mm_SafeString {
    PROP(vtable,     0x0, void *);
    PROP(str,        0x8, const char *);
    PSEUDO_TYPE_SIZE(0x10);
};

// Lp::Utl::StateMachine::Delegate<T>
struct mm_StateMachineDelegate {
    PROP(vtable,     0x0,  void *);
    PROP(owner,      0x8,  void *);
    PROP(enter,      0x10, mm_pointer_to_member_function);
    PROP(exec,       0x20, mm_pointer_to_member_function);
    PROP(exit,       0x30, mm_pointer_to_member_function);
    PSEUDO_TYPE_SIZE(0x40);

    mm_pointer_to_member_function &callback_n(size_t n) {
        switch (n) {
            case 0: return enter();
            case 1: return exec();
            case 2: return exit();
            default: panic("invalid");
        }
    }
};

// Lp::Utl::StateMachine
struct mm_StateMachine {
    PROP(state,   0x8, uint32_t);
    PROP(states, 0x28, mm_count_ptr<mm_StateMachineDelegate>);
    PROP(names,  0x38, mm_count_ptr<mm_SafeString>);
    PSEUDO_TYPE_SIZE(0x48);
};

struct mm_hitbox {
    PROP(vtable, 0x0, uintptr_t);
    PSEUDO_TYPE_SIZE(0x1c8);

    static constexpr uintptr_t vtable_offset[VER_COUNT] = {
        [VER_301] = 0x028694a0,
    };

    void verify() {
        uintptr_t offset = vtable() - s_target_start;
        if (offset != assert_nonzero(vtable_offset[s_cur_mm_version])) {
            panic("mm_hitbox unexpected vtable %#lx", offset);
        }
    }
};

struct mm_hitbox_node {
    PROP(node,     0, mm_ListNode);
    PROP(owner, 0x10, mm_hitbox *);
    PROP(list,  0x18, void *);
    PSEUDO_TYPE_UNSIZED;
};

struct mm_hitbox_manager {
    PROP(split_lists,  0x10, mm_list<mm_hitbox_node, /*use_offset_to_link*/ false>[4]);
    PROP(staging_list, 0x70, mm_list<mm_hitbox>);
    PSEUDO_TYPE_UNSIZED;
};

struct mm_normal_collider;
struct mm_scol_collider;

struct mm_block_collider_owner {
    PROP(collider, 0x38, mm_normal_collider);
    PSEUDO_TYPE_UNSIZED;
};

struct mm_terrain_manager {
    PROP(block_collider_owners, 0x18, mm_count_ptr<mm_block_collider_owner *>);
    PSEUDO_TYPE_UNSIZED;
};

// This is really one of two unrelated structs, and the real way to distinguish
// them is via the vtable on the node, but distinguishing them by their own
// vtables is just a bit easier...
struct mm_some_collider {
    PROP(vtable, 0, void *);
    PSEUDO_TYPE_UNSIZED;

    std::variant<mm_normal_collider *, mm_scol_collider *, std::monostate> downcast();
    uintptr_t vt20_offset() const {
        uintptr_t vt20 = *(uintptr_t *)((char *)vtable() + 0x20);
        return vt20 - s_target_start;
    }
};

struct mm_normal_collider : public mm_some_collider {
    static constexpr size_t initial_dump_size = 0x3e0;

    PROP(ext_pos_cur, 0x290, float *);
    PROP(ext_pos_old, 0x298, float *);

    // virtual method @ 0x20 for all subclasses:
    // (we don't care about the specific subclass for now)
    static constexpr uintptr_t vt20_offset[VER_COUNT] = {
        [VER_301] = 0xdd0e00,
    };
};

struct mm_scol_collider : public mm_some_collider {
    static constexpr size_t initial_dump_size = 0x3e8;

    PROP(ext_pos_cur, 0x2a0, float *);
    PROP(ext_pos_old, 0x2a8, float *);

    // virtual method @ 0x20 for two subclasses:
    // (we don't care about the specific subclass for now)
    static constexpr uintptr_t vt20_offset_1[VER_COUNT] = {
        [VER_301] = 0xe192e0,
    };
    // virtual method @ 0x20 for another subclass:
    static constexpr uintptr_t vt20_offset_2[VER_COUNT] = {
        [VER_301] = 0xe23a70,
    };

};

// Figure out what type of object this is.
std::variant<mm_normal_collider *, mm_scol_collider *, std::monostate>
mm_some_collider::downcast() {
    uintptr_t vt20_offset = this->vt20_offset();
    if (vt20_offset == assert_nonzero(mm_normal_collider::vt20_offset[s_cur_mm_version])) {
        return (mm_normal_collider *)this;
    } else if (vt20_offset == assert_nonzero(mm_scol_collider::vt20_offset_1[s_cur_mm_version]) ||
               vt20_offset == assert_nonzero(mm_scol_collider::vt20_offset_2[s_cur_mm_version])) {
        return (mm_scol_collider *)this;
    } else {
        // unknown type
        return std::monostate{};
    }
}

// TODO: cleanup naming for all collider stuff :(

struct mm_some_collider_node;

struct mm_some_collider_node_outer {
    PROP(node,  0x20, mm_some_collider_node);
    PROP(owner, 0x48, mm_some_collider *);
    PSEUDO_TYPE_UNSIZED;
};

struct mm_some_collider_node {
    PROP(list_node, 0x0,  mm_ListNode);
    PROP(outer,     0x10, mm_some_collider_node_outer *);
    PSEUDO_TYPE_UNSIZED;
};

struct mm_BgCollisionSystem {
    PROP(colliders1, 0x38, mm_list<mm_some_collider_node>);
    PROP(colliders2, 0x58, mm_list<mm_some_collider_node>);

    PSEUDO_TYPE_UNSIZED;
};

struct mm_AreaSystem {
    PROP(world_id,            0x18, uint32_t);
    PROP(hitbox_mgr,          0x40, mm_hitbox_manager *);
    PROP(bg_collision_system, 0x90, mm_BgCollisionSystem *);
    PROP(terrain_mgr,         0xa0, mm_terrain_manager *);
    PSEUDO_TYPE_UNSIZED;
};

static const char *get_state_name(mm_StateMachine *sm, uint32_t state) {
    if (state < sm->names().count) {
        return sm->names().ptr[state].str();
    } else {
        return "?";
    }
}

__attribute__((noinline))
static uintptr_t get_state_callback(mm_StateMachine *sm, uint32_t state, uint32_t which_callback) {
    EXL_ASSERT(state < sm->states().count);
    mm_StateMachineDelegate *delegate = &sm->states().ptr[state];
    mm_pointer_to_member_function cb = delegate->callback_n(which_callback);
    uintptr_t func = (uintptr_t)cb.resolve(delegate->owner());
    if (func) {
        func -= s_target_start; // unslide address
    }
    return func;
}

static void *return_address_from_frame_impl(void *frame0, size_t n) {
    while (n--) {
        if (!frame0)
            return nullptr;
        frame0 = ((void **)frame0)[0];
    }
    if (!frame0)
        return nullptr;
    return ((void **)frame0)[1];
}

#define return_address_from_frame(n) \
    return_address_from_frame_impl(__builtin_frame_address(0), n)

HOOK_DEFINE_TRAMPOLINE(Stub_StateMachine_changeState) {
    static void Callback(mm_StateMachine *sm, int state) {
        int old_state = sm->state();
        xprintf("%p.set_state(%s(%d) -> %s(%d) in:0x%lx tick:0x%lx out:0x%lx (obj:%p)) <- %p <- %p <- %p <- %p <- %p",
            sm,
            get_state_name(sm, old_state), old_state,
            get_state_name(sm, state), state,
            get_state_callback(sm, state, 0),
            get_state_callback(sm, state, 1),
            get_state_callback(sm, state, 2),
            sm->states().ptr[state].owner(),
            __builtin_return_address(0),
            return_address_from_frame(0),
            return_address_from_frame(1),
            return_address_from_frame(2),
            return_address_from_frame(3)
        );
        Orig(sm, state);
    }
    static constexpr uintptr_t offset[VER_COUNT] = {
        [VER_301] = 0x8b9280,
    };
};

template <typename StubFoo>
void install() {
    uintptr_t offset = StubFoo::offset[s_cur_mm_version];
    assert(offset);
    StubFoo::InstallAtOffset(assert_nonzero(StubFoo::offset[s_cur_mm_version]));
}

// just a simple hash table
struct SeenCache {
    struct Entry {
        uint64_t object:48,
                  frame:16;
        uint64_t value;
    };

    bool test_and_set(void *object) {
        auto [entry, found] = find_entry(object);
        if (!found) {
            if (count_ >= MAX_COUNT) {
                xprintf("hash was full");
                return true;
            }
            entry->frame = cur_frame_;
            entry->object = (uintptr_t)object;
            count_++;
            return false;
        }
        return true;
    }

    std::tuple<Entry *, bool> find_entry(void *object) {
        assert(object != nullptr);
        uintptr_t object_up = (uintptr_t)object;
        assert(object_up < ((uintptr_t)1 << 48));
        size_t idx = hash(object);
        size_t nchecked = 0;
        uint64_t cur_frame = cur_frame_;
        while (nchecked < NUM_ENTRIES) {
            Entry *ent = &entries_[idx];
            if (ent->object == 0 || ent->frame != cur_frame) {
                return std::make_tuple(ent, false);
            }
            if (ent->object == object_up) {
                return std::make_tuple(ent, true);
            }

            idx = (idx + 1) % NUM_ENTRIES;
            nchecked++;
        }
        panic("table was unexpectedly full");
    }

    size_t hash(void *object) {
        return (uintptr_t)object % NUM_ENTRIES;
    }

    void next_frame() {
        cur_frame_++;

        // Clear out entries on a rolling basis.
        // Must visit the entire array at least once every `visit_frames` frames:
        size_t visit_frames = FRAME_ROLLOVER_COUNT - MAX_AGE;
        size_t entries_per_frame = (NUM_ENTRIES + visit_frames - 1) / visit_frames;
        for (size_t i = 0; i < entries_per_frame; i++) {
            size_t idx = clearout_idx_++;
            if (clearout_idx_ == NUM_ENTRIES) {
                clearout_idx_ = 0;
            }
            if (age(entries_[idx]) > MAX_AGE) {
                entries_[idx] = Entry{};
                count_ = 0;
            }
        }
    }

    size_t age(Entry &entry) {
        return (uint16_t)(cur_frame_ - entry.frame);
    }

    static constexpr size_t NUM_ENTRIES = 24593;
    static constexpr size_t MAX_COUNT = NUM_ENTRIES / 2;
    static constexpr size_t FRAME_ROLLOVER_COUNT = 1 << 16;
    static constexpr size_t MAX_AGE = 1;

    std::array<Entry, NUM_ENTRIES> entries_{};
    uint16_t cur_frame_ = 0;
    size_t count_ = 0;
    size_t clearout_idx_ = 0;
};

SeenCache s_seen_cache;

static void report_hitbox(mm_hitbox *hb, bool surprise) {
    hb->verify();
    if (!s_seen_cache.test_and_set(hb)) {
        if (surprise) {
            xprintf("surprising hitbox %p", hb);
        }
        s_hose.write_packet([&](auto &w) {
            w.write_tag({"hitbox"});
            w.write_prim(hb);
            w.write_range(hb, (char *)hb + pt_size_of<mm_hitbox>);
        });
    }
}

HOOK_DEFINE_TRAMPOLINE(Stub_hitbox_collide) {
    static long Callback(mm_hitbox *hb1, mm_hitbox *hb2) {
        long ret = Orig(hb1, hb2);
        report_hitbox(hb1, /*surprise*/ true);
        report_hitbox(hb2, /*surprise*/ true);
        s_hose.write_packet([&](auto &w) {
            w.write_tag({"collisi"});
            w.write_prim(hb1);
            w.write_prim(hb2);
            w.write_prim(ret);
        });
        return ret;
    }
    static constexpr uintptr_t offset[VER_COUNT] = {
        [VER_301] = 0xe28a50,
    };
};

static void report_all_hitboxes(mm_AreaSystem *as) {
    for (mm_list<mm_hitbox_node, false> &list : as->hitbox_mgr()->split_lists()) {
        for (mm_hitbox_node &hn : list) {
            report_hitbox(hn.owner(), /*surprise*/ false);
        }
    }
    for (mm_hitbox &hb : as->hitbox_mgr()->staging_list()) {
        report_hitbox(&hb, /*surprise*/ false);
    }
}
static void report_some_collider(mm_some_collider *some, int which_list) {
    static const float dummy_pos[2]{};
    if (s_seen_cache.test_and_set(some)) {
        return;
    }
    s_hose.write_packet([&](auto &w) {
        w.write_tag({"uhh"});
        w.write_prim(some);
    });
    return;
    auto downcasted = some->downcast();
    if (auto ncp = std::get_if<mm_normal_collider *>(&downcasted)) {
        mm_normal_collider *nc = *ncp;
        s_hose.write_packet([&](auto &w) {
            w.write_tag({"normcol"});
            w.write_prim(nc);
            w.write_raw(nc, mm_normal_collider::initial_dump_size);
            w.write_prim(nc->ext_pos_cur());
            w.write_n(nc->ext_pos_cur() ?: dummy_pos, 2);
            w.write_prim(nc->ext_pos_old());
            w.write_n(nc->ext_pos_old() ?: dummy_pos, 2);
        });
    } else if (auto scp = std::get_if<mm_scol_collider *>(&downcasted)) {
        mm_scol_collider *sc = *scp;
        s_hose.write_packet([&](auto &w) {
            w.write_tag({"scolcol"});
            w.write_prim(sc);
            w.write_raw(sc, mm_scol_collider::initial_dump_size);
            w.write_prim(sc->ext_pos_cur());
            w.write_n(sc->ext_pos_cur() ?: dummy_pos, 2);
            w.write_prim(sc->ext_pos_old());
            w.write_n(sc->ext_pos_old() ?: dummy_pos, 2);
        });
    } else {
        xprintf("unknown collider %p with vtable %#lx", some, some->vt20_offset());
        s_hose.write_packet([&](auto &w) {
            w.write_tag({"unkcol"});
            w.write_prim(some);
        });
    }
}

static void report_all_colliders_in(mm_list<mm_some_collider_node> *cnlist, int which_list) {
    for (mm_some_collider_node &cn : *cnlist) {
        mm_some_collider *some = cn.outer()->owner();
        report_some_collider(some, which_list);
    }
}

static void report_all_colliders(mm_AreaSystem *as) {
    report_all_colliders_in(&as->bg_collision_system()->colliders1(), 1);
    report_all_colliders_in(&as->bg_collision_system()->colliders2(), 2);
    for (mm_block_collider_owner *owner : as->terrain_mgr()->block_collider_owners()) {
        report_some_collider(&owner->collider(), 2);
    }
}

HOOK_DEFINE_TRAMPOLINE(Stub_AreaSystem_do_many_collisions) {
    static void Callback(mm_AreaSystem *self) {
        s_seen_cache.next_frame();
        s_hose.write_packet([&](auto &w) {
            w.write_tag({"do_many"});
            w.write_prim(self);
            w.write_prim((uint64_t)self->world_id());
        });
        report_all_colliders(self);
        report_all_hitboxes(self);
        Orig(self);
    }
    static constexpr uintptr_t offset[VER_COUNT] = {
        [VER_301] = 0xe44320,
    };
};

extern "C" void exl_main(void* x0, void* x1) {
    /* Setup hooking enviroment. */
    s_target_start = exl::util::modules::GetTargetStart();
    xprintf("exl_main, TS=%lx", s_target_start);
    exl::hook::Initialize();

    serve_main();

    //install<Stub_StateMachine_changeState>();

    // this is for 3.0.1:

    {
        // Patch to skip intro cutscene
        // This function is the tick callback for UIBootSceneSeq state cIdle.
        // The patch makes it switch to cDisp when it ould otherwise switch to cAppear.
        exl::patch::CodePatcher p(0x017e428c);
        p.Write<uint32_t>(0x321e03e1); // orr w1, wzr, #4 (instead of 2)
    }

    // Store 0 to timer instead of the normal animation length
    exl::patch::CodePatcher(0x012dcc6c).Write<uint32_t>(0xf900581f); // cPose
    exl::patch::CodePatcher(0x012dc9e8).Write<uint32_t>(0xf9005a7f); // cFall


    // TODO: make these dynamic hooks
    install<Stub_hitbox_collide>();
    install<Stub_AreaSystem_do_many_collisions>();
    log_str("done hooking");
}

extern "C" NORETURN void exl_exception_entry() {
    /* TODO: exception handling */
    log_str("exl_exception_entry");
    EXL_ABORT(0x420);
}

// tilted blocks: p/x *(int*)($slide+0x00da3ae8 ) = 0x1e2a1000

