#include "main.hpp"
#include "lib.hpp"
#include "serve.hpp"
#include "stuff.hpp"
#include "generated.hpp"
#include <stdarg.h>
#include <array>
#include <variant>
#include <optional>
#include <utility>
#include "../../externals/xxhash/xxhash.h"
#include "stupid_hash.hpp"

std::array<std::optional<BuildId>, exl::util::mem_layout::s_MaxModules> g_build_ids;

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

static mm_version s_cur_mm_version;
static uintptr_t s_target_start;

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

    void verify() {
        uintptr_t addr = vtable();
        if (addr != mm_addrs::hitbox_vtable()) {
            panic("mm_hitbox unexpected vtable %#lx", addr);
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

struct mm_collider_segment {
    PSEUDO_TYPE_SIZE(0x18);
};


// This is really one of two unrelated structs, and the real way to distinguish
// them is via the vtable on the node, but distinguishing them by their own
// vtables is just a bit easier...
struct mm_some_collider {
    PROP(vtable, 0, void *);
    PSEUDO_TYPE_UNSIZED;

    std::variant<mm_normal_collider *, mm_scol_collider *, std::monostate> downcast();
    uintptr_t vt20() const {
        // just a random vtable method that doesn't differ between subclasses too much
        return *(uintptr_t *)((char *)vtable() + 0x20);
    }
};

struct mm_normal_collider : public mm_some_collider {
    PROP(ext_pos_cur, 0x290, float *);
    PROP(ext_pos_old, 0x298, float *);

    PROP(segments_cur, 0x3c0, mm_count_ptr<mm_collider_segment>);
    PROP(segments_old, 0x3d0, mm_count_ptr<mm_collider_segment>);

    static constexpr size_t initial_dump_size = 0x3e0;
};

struct mm_scol_collider : public mm_some_collider {
    static constexpr size_t initial_dump_size = 0x103c;

    PROP(ext_pos_cur, 0x2a0, float *);
    PROP(ext_pos_old, 0x2a8, float *);
};

// Figure out what type of object this is.
std::variant<mm_normal_collider *, mm_scol_collider *, std::monostate>
mm_some_collider::downcast() {
    uintptr_t vt20 = this->vt20();
    if (vt20 == mm_addrs::normal_collider_vt20()) {
        return (mm_normal_collider *)this;
    } else if (vt20 == mm_addrs::scol_vt20() ||
               vt20 == mm_addrs::scol_subclass_vt20()) {
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
    static constexpr auto GetAddr = &mm_addrs::StateMachine_changeState;
};

template <typename StubFoo>
void install() {
    StubFoo::InstallAtPtr(StubFoo::GetAddr());
}

// https://planetmath.org/goodhashtableprimes
static StupidHash<UInt48, Nothing, 12289> s_colliders;
static StupidHash<UInt48, uint64_t, 3079> s_hashed_scols;
static StupidHash<UInt48, Nothing, 3079> s_hitboxes_this_frame;

void note_new_hose_connection() { // run on hose thread
    g_cur_rpc_flags.fetch_or(RPC_FLAG_SEND_COLLS, std::memory_order_release);
}

static void report_hitbox(mm_hitbox *hb, bool surprise) {
    hb->verify();
    if (s_hitboxes_this_frame.lookup(UInt48(hb), /*insert*/ true).second) {
        // already seen
        return;
    }
    if (surprise) {
        xprintf("surprising hitbox %p", hb);
    }
    s_hose.write_packet([&](auto &w) {
        w.write_tag({"hitbox"});
        w.write_prim(hb);
        w.write_prim(*hb);
    });
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
    static constexpr auto GetAddr = &mm_addrs::hitbox_collide;
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

static bool write_cached_dump(auto *entry, bool found, auto &&write_callback) {
    std::optional<uint64_t> old_hash;
    if (found) {
        old_hash = entry->value;
    }
    bool ret;
    s_hose.write_packet(
        std::move(write_callback),
        [&](uint8_t *buf, size_t len) -> bool {
            uint64_t new_hash = XXH3_64bits(buf, len);
            if (entry) {
                entry->value = new_hash;
            }
            // only send if this is new
            ret = old_hash != new_hash;
            // if (ret) { xprintf("sending new %p", buf); }
            return ret;
        }
    );
    return ret;
}

static const float s_dummy_pos[2]{};

static void write_normal_collider(mm_normal_collider *nc, tag8 tag) {
    s_hose.write_packet([&](auto &w) {
        w.write_tag(tag);
        w.write_prim(nc);
        w.write_raw(nc, mm_normal_collider::initial_dump_size);
        w.write_n(nc->ext_pos_cur() ?: s_dummy_pos, 2);
        w.write_n(nc->ext_pos_old() ?: s_dummy_pos, 2);
        w.write_n(nc->segments_cur().ptr, nc->segments_cur().count);
        w.write_n(nc->segments_old().ptr, nc->segments_old().count);
    });
}

static void frame_start_actions() {
    s_hitboxes_this_frame.clear();
    if (test_and_clear_rpc_flag(RPC_FLAG_SEND_COLLS)) {
        s_hashed_scols.clear();
        for (auto &entry : s_colliders) {
            auto nc = (mm_normal_collider *)entry.key;
            write_normal_collider(nc, {"normco*"});
        }

    }
}

HOOK_DEFINE_TRAMPOLINE(Stub_Collider_add_to_collision_grid) {
    static void Callback(mm_normal_collider *self) {
        Orig(self);
        if (s_colliders.lookup(UInt48(self), /*insert*/ true).second) {
            xprintf("%p added but already in s_colliders", self);
        }
        write_normal_collider(self, {"normco+"});
    }
    static constexpr auto GetAddr = &mm_addrs::Collider_add_to_collision_grid;
};

HOOK_DEFINE_TRAMPOLINE(Stub_Collider_remove_from_collision_grid_and_lists) {
    static void Callback(mm_normal_collider *self) {
        auto [entry, _] = s_colliders.lookup(UInt48(self), /*insert*/ false);
        if (entry) {
            s_colliders.remove(entry);
            s_hose.write_packet([&](auto &w) {
                w.write_tag({"normco-"});
                w.write_prim(self);
            });
        } else {
            // this happens at startup.
            //xprintf("%p removed but not in s_colliders", self);
        }
        Orig(self);
    }
    static constexpr auto GetAddr = &mm_addrs::Collider_remove_from_collision_grid_and_lists;
};

HOOK_DEFINE_TRAMPOLINE(Stub_scol_true_outmost) {
    static void Callback(mm_scol_collider *self) {
        Orig(self);
        auto [entry, found] = s_hashed_scols.lookup(UInt48(self), /*insert*/ true);
        bool sent = write_cached_dump(entry, found, [&](auto &w){
            w.write_tag({"scolcol"});
            w.write_prim(self);
            w.write_raw(self, mm_scol_collider::initial_dump_size);
            w.write_n(self->ext_pos_cur() ?: s_dummy_pos, 2);
            w.write_n(self->ext_pos_old() ?: s_dummy_pos, 2);
        });
        if (!sent) {
            s_hose.write_packet([&](auto &w) {
                w.write_tag({"scolco~"});
                w.write_prim(self);
            });
        }
    }
    static constexpr auto GetAddr = &mm_addrs::scol_true_outmost;
};

HOOK_DEFINE_TRAMPOLINE(Stub_AreaSystem_do_many_collisions) {
    static void Callback(mm_AreaSystem *self) {
        if (self->world_id() == 0) {
            frame_start_actions();
        }
        s_hose.write_packet([&](auto &w) {
            w.write_tag({"do_many"});
            w.write_prim(self);
            w.write_prim((uint64_t)self->world_id());
        });
        report_all_hitboxes(self);
        Orig(self);
    }
    static constexpr auto GetAddr = &mm_addrs::AreaSystem_do_many_collisions;
};

static void fetch_build_ids() {
    for (int mod_idx = 0; mod_idx < exl::util::mem_layout::s_ModuleCount; mod_idx++) {
        const exl::util::Range &rodata = exl::util::GetModuleInfo(mod_idx).m_Rodata;

        constexpr char gnu_tag[4] = "GNU";
        size_t needle_size = sizeof(gnu_tag) + sizeof(BuildId);
        if (rodata.m_Size < needle_size) {
            continue;
        }
        size_t haystack_size = std::min(rodata.m_Size, (size_t)0x1000) - needle_size;
        uintptr_t haystack_ptr = rodata.GetEnd() - needle_size;
        for (size_t i = 0; i < haystack_size; i++) {
            if (!memcmp((void *)haystack_ptr, gnu_tag, sizeof(gnu_tag))) {
                BuildId &build_id = g_build_ids.at(mod_idx).emplace();
                memcpy(&build_id, (void *)(haystack_ptr + sizeof(gnu_tag)), sizeof(build_id));
            }
            haystack_ptr--;
        }
    }
}

static void init_version() {
    const BuildId &build_id = g_build_ids.at(exl::util::mem_layout::s_MainModuleIdx).value();
    for (uint8_t i = 0; i < VER_COUNT; i++) {
        if (s_version_to_build_id[i] == build_id) {
            s_cur_mm_version = (mm_version)i;
            return;
        }
    }
    panic("unknown build ID");
}


uintptr_t get_addr_impl(const uintptr_t (&by_ver)[VER_COUNT], const char *name) {
    uintptr_t offset = by_ver[s_cur_mm_version];
    if (offset == MISSING_ADDR) {
        panic("missing %s in addrs.yaml for this version", name);
    }
    return offset + s_target_start;
}

extern "C" void exl_main(void* x0, void* x1) {
    s_target_start = exl::util::modules::GetTargetStart();
    xprintf("exl_main, TS=%lx", s_target_start);
    fetch_build_ids();
    init_version();

    exl::hook::Initialize();

    serve_main();

    //install<Stub_StateMachine_changeState>();

    // this is for 3.0.1 (TODO: make version-dependent):

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
    install<Stub_Collider_add_to_collision_grid>();
    install<Stub_Collider_remove_from_collision_grid_and_lists>();
    install<Stub_scol_true_outmost>();
    log_str("done hooking");
}

extern "C" NORETURN void exl_exception_entry() {
    /* TODO: exception handling */
    log_str("exl_exception_entry");
    EXL_ABORT(0x420);
}

