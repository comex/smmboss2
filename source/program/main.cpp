#include "lib.hpp"
#include "serve.hpp"
#include "stuff.hpp"
#include <stdarg.h>
#include <array>

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

namespace mm {

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

union pointer_to_member_function {
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
static_assert(sizeof(pointer_to_member_function) == 0x10);

// sead::ListNode
struct ListNode {
    ListNode *prev;
    ListNode *next;
};

// sead::ListImpl
struct ListImpl {
    ListNode head;
    uint32_t count;
    uint32_t offset_to_link;
};

// wrapper, no evidence for original
template <typename T>
struct list_iterator {
    ListNode *cur_node_;
    uint32_t offset_to_link_;
    ListNode *end_;

    T &operator*() const {
        assert(cur_node_ != end_);
        return *(T *)((char *)cur_node_ - offset_to_link_);
    }
    list_iterator &operator++() {
        assert(cur_node_ != end_);
        cur_node_ = cur_node_->next;
        return *this;
    }
    bool operator==(const list_iterator &other) const {
        return cur_node_ == other.cur_node_;
    };
};

// wrapper, no evidence for original
template <typename T>
struct list : public ListImpl {
    list_iterator<T> begin() { return list_iterator<T>{head.next, offset_to_link, &head}; }
    list_iterator<T> end()   { return list_iterator<T>{&head, offset_to_link, &head}; }
};

struct SafeString {
    PROP(vtable,     0x0, void *);
    PROP(str,        0x8, const char *);
    PSEUDO_TYPE_SIZE(0x10);
};

// Lp::Utl::StateMachine::Delegate<T>
struct StateMachineDelegate {
    PROP(vtable,     0x0,  void *);
    PROP(owner,      0x8,  void *);
    PROP(enter,      0x10, pointer_to_member_function);
    PROP(exec,       0x20, pointer_to_member_function);
    PROP(exit,       0x30, pointer_to_member_function);
    PSEUDO_TYPE_SIZE(0x40);

    pointer_to_member_function &callback_n(size_t n) {
        switch (n) {
            case 0: return enter();
            case 1: return exec();
            case 2: return exit();
            default: panic("invalid");
        }
    }
};

template <typename T>
struct CountPtr {
    uint32_t count;
    pt_pointer<T> ptr;
};

// Lp::Utl::StateMachine
struct StateMachine {
    PROP(state,   0x8, uint32_t);
    PROP(states, 0x28, CountPtr<StateMachineDelegate>);
    PROP(names,  0x38, CountPtr<SafeString>);
    PSEUDO_TYPE_SIZE(0x48);
};

struct hitbox {
    PROP(vtable, 0x0, uintptr_t);
    PSEUDO_TYPE_SIZE(0x1c8);

    static constexpr uintptr_t vtable_offset[VER_COUNT] = {
        [VER_301] = 0x028694a0,
    };

    void verify() {
        assert(vtable() == exl::util::modules::GetTargetStart() +
                           vtable_offset[s_cur_mm_version]);
    };
};

struct hitbox_node {
    PROP(node,     0, ListNode);
    PROP(owner, 0x10, hitbox *);
    PROP(list,  0x18, void *);
    PSEUDO_TYPE_UNSIZED;
};

struct hitbox_manager {
    PROP(lists,  0x10, list<hitbox_node>[5]);
    PSEUDO_TYPE_UNSIZED;
};

struct AreaSystem {
    PROP(world_id,   0x18, uint32_t);
    PROP(hitbox_mgr, 0x40, hitbox_manager *);
    PSEUDO_TYPE_UNSIZED;
};

} // namespace mm

static const char *get_state_name(mm::StateMachine *sm, uint32_t state) {
    if (state < sm->names().count) {
        return sm->names().ptr[state].str();
    } else {
        return "?";
    }
}

__attribute__((noinline))
static uintptr_t get_state_callback(mm::StateMachine *sm, uint32_t state, uint32_t which_callback) {
    EXL_ASSERT(state < sm->states().count);
    mm::StateMachineDelegate *delegate = &sm->states().ptr[state];
    mm::pointer_to_member_function cb = delegate->callback_n(which_callback);
    uintptr_t func = (uintptr_t)cb.resolve(delegate->owner());
    if (func) {
        func -= exl::util::modules::GetTargetStart(); // unslide address
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
    static void Callback(mm::StateMachine *sm, int state) {
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
    StubFoo::InstallAtOffset(offset);
}

struct SeenCache {
    struct Entry {
        uintptr_t object:48,
                  frame:16;
    };

    bool test_and_set(void *object) {
        auto [entry, found] = find_entry(object);
        if (!found) {
            if (count_ >= MAX_COUNT) {
                xprintf("hash was full");
                return true;
            }
            entry->frame = cur_frame_ % (1 << 16);
            entry->object = (uintptr_t)object;
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
        count_ = 0;

        // clear out entries on a rolling basis
        size_t possible_frames = 1 << 16;
        size_t to_clear_per_frame = (NUM_ENTRIES + possible_frames - 1) / possible_frames;
        // ^ might just be 1
        for (size_t i = cur_frame_ * to_clear_per_frame;
                    i < (cur_frame_ + 1) * to_clear_per_frame && i < NUM_ENTRIES;
                    i++) {
            entries_[i] = Entry{};
        }
    }

    static constexpr size_t NUM_ENTRIES = 24593;
    static constexpr size_t MAX_COUNT = NUM_ENTRIES / 2;
    std::array<Entry, NUM_ENTRIES> entries_{};
    uint16_t cur_frame_ = 0;
    size_t count_ = 0;
};

SeenCache s_seen_cache;

static void report_hitbox(mm::hitbox *hb, bool surprise) {
    hb->verify();
    if (!s_seen_cache.test_and_set(hb)) {
        if (surprise) {
            xprintf("surprising hitbox %p", hb);
        }
        s_hose.write_fixed(
            hose_tag{"hitbox"},
            hb,
            *hb
        );
    }
}

HOOK_DEFINE_TRAMPOLINE(Stub_hitbox_collide) {
    static long Callback(mm::hitbox *hb1, mm::hitbox *hb2) {
        long ret = Orig(hb1, hb2);
        report_hitbox(hb1, /*surprise*/ true);
        report_hitbox(hb2, /*surprise*/ true);
        s_hose.write_fixed(
            hose_tag{"collisi"},
            hb1,
            hb2,
            ret
        );
        return ret;
    }
    static constexpr uintptr_t offset[VER_COUNT] = {
        [VER_301] = 0xe28a50,
    };
};

HOOK_DEFINE_TRAMPOLINE(Stub_AreaSystem_do_many_collisions) {
    static void Callback(mm::AreaSystem *self) {
        s_seen_cache.next_frame();
        s_hose.write_fixed(
            hose_tag{"do_many"},
            self,
            (uint64_t)self->world_id()
        );
        for (mm::list<mm::hitbox_node> &list : self->hitbox_mgr()->lists()) {
            for (mm::hitbox_node &hn : list) {
                report_hitbox(hn.owner(), /*surprise*/ false);
            }
        }
        Orig(self);
    }
    static constexpr uintptr_t offset[VER_COUNT] = {
        [VER_301] = 0xe44320,
    };
};

extern "C" void exl_main(void* x0, void* x1) {
    /* Setup hooking enviroment. */
    xprintf("exl_main, TS=%lx", exl::util::modules::GetTargetStart());
    exl::hook::Initialize();

    serve_main();

    install<Stub_StateMachine_changeState>();

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

