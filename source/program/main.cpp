#include "lib.hpp"
#include "serve.hpp"
#include <stdarg.h>

namespace nn::diag::detail {
    int PrintDebugString(const char *);
}

union state_obj_callback {
    // this is actually a pointer-to-member-function
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
};
static_assert(sizeof(union state_obj_callback) == 0x10);

struct state_obj {
    void *vt;
    void *self;
    union state_obj_callback callbacks[3];
};
static_assert(sizeof(struct state_obj) == 0x40);


struct string {
    void *vt;
    const char *str;
};

struct statemgr {
    void *vtable;
    int state;
    int counter;
    int f10;
    int f14;
    int f18;
    int f1c;
    char f21;
    char pad22[7];
    int state_objs_count;
    int pad28;
    struct state_obj *state_objs;
    int names_count;
    int pad3c;
    struct string *names;
};
static_assert(sizeof(struct statemgr) == 0x48);

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

static const char *get_state_name(struct statemgr *smgr, int state) {
    if (state >= 0 && state < smgr->names_count)
        return smgr->names[state].str;
    else
        return "?";
}

__attribute__((noinline))
static uintptr_t get_state_callback(struct statemgr *smgr, int state, int which_callback) {
    EXL_ASSERT(state >= 0 && state < smgr->state_objs_count);
    struct state_obj *obj = &smgr->state_objs[state];
    union state_obj_callback *cb = &obj->callbacks[which_callback];
    uintptr_t func = 0;
    if (cb->is_virt) {
        void *self = obj->self;
        uintptr_t vtable = *(uintptr_t *)((char *)self + cb->virt.offset_to_vtable);
        func = *(uintptr_t *)(vtable + cb->virt.vtable_offset);
    } else {
        func = cb->nonvirt.func;
    }
    if (func)
        func -= exl::util::modules::GetTargetStart(); // unslide address
    return func;
}

static void *
return_address_from_frame_impl(void *frame0, size_t n) {
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

HOOK_DEFINE_TRAMPOLINE(StubStatemgrSetState) {
    static void Callback(struct statemgr *smgr, int state) {
        int old_state = smgr->state;
        xprintf("%p.set_state(%s(%d) -> %s(%d) in:0x%lx tick:0x%lx out:0x%lx (obj:%p)) <- %p <- %p <- %p <- %p <- %p",
            smgr,
            get_state_name(smgr, old_state), old_state,
            get_state_name(smgr, state), state,
            get_state_callback(smgr, state, 0),
            get_state_callback(smgr, state, 1),
            get_state_callback(smgr, state, 2),
            smgr->state_objs[state].self,
            __builtin_return_address(0),
            return_address_from_frame(0),
            return_address_from_frame(1),
            return_address_from_frame(2),
            return_address_from_frame(3)
        );
        Orig(smgr, state);
    }
};

HOOK_DEFINE_TRAMPOLINE(StubOpenFile) {
    static long Callback(struct string *name, long x1, long x2, long x3, long x4, long x5) {
        char mine[256];
        const char *orig = name->str;
        if (orig) {
            size_t len = strlen(orig);
            if (len < sizeof(mine) - 1) {
                memcpy(mine, orig, len);
                mine[len] = '\0';
                while (char *s = strstr(mine, "WU")) {
                    s[0] = 'M';
                    s[1] = '1';
                }
                name->str = mine;
                long ret = Orig(name, x1, x2, x3, x4, x5);
                xprintf("open_file(%s) => %lx", name->str, ret);
                name->str = orig;
                if (ret) {
                    return ret;
                }
            }
        }
        long ret = Orig(name, x1, x2, x3, x4, x5);
        xprintf("open_file(%s) => %lx", name->str, ret);
        return ret;
    }
};

HOOK_DEFINE_TRAMPOLINE(StubSearchAssetCallTableByName) {
    static void *Callback(void *out, void *self, const char *name) {
        xprintf("searchAssetCallTableByName(%s)", name);
        return Orig(out, self, name);
    }
};

HOOK_DEFINE_TRAMPOLINE(Stub_xlink2_System_setGlobalPropertyValue) {
    static void Callback(void *self, int property_id, int value) {
        xprintf("setGlobalPropertyValue(%p, %d, %d)", self, property_id, value);
        if (property_id == 0) {
            value = 3; // NSMBU
        }
        Orig(self, property_id, value);
    }
};

HOOK_DEFINE_TRAMPOLINE(StubGetBlockInfo) {
    static int *Callback(void *block) {
        static int x = 0x100009;
        return &x;
    }
};

HOOK_DEFINE_TRAMPOLINE(Stub_gsys_Model_create) {
    static void *Callback(struct string *name, void *create_arg, void *heap) {
        void *ret = Orig(name, create_arg, heap);
        xprintf("gsys::Model::create(name=%s) => %p", name->str, ret);
        return ret;
    }
};
HOOK_DEFINE_TRAMPOLINE(Stub_gsys_Model_pushBack) {
    static void *Callback(void *self, void *model_resource, struct string *name, void *heap) {
        xprintf("-- gsys::Model::pushBack(%p, name=%s)", self, name->str);
        return Orig(self, model_resource, name, heap);
    }
};

struct NVNmemoryPool {
    char x0[0x10];
    char flags;
    char x11[0x70-0x11];
    char *ptr;
};

struct agl_VertexBuffer {
    char x0[0xf8];
    int offset;
    int xfc;
    NVNmemoryPool *pool;
};


HOOK_DEFINE_TRAMPOLINE(Stub_agl_VertexBuffer_flushCPUCache) {
    static void Callback(agl_VertexBuffer *self, int offset, long size) {
        NVNmemoryPool *pool = self->pool;
        if (pool && ((pool->flags & 5) == 4) && pool->ptr) {
            char *ptr = pool->ptr;
            static volatile void *ptr_to_corrupt;
            xprintf("flushCPUCache(offset=%#x+%#x, size=%#lx): data=%p <%p>", self->offset, offset, size, ptr, &ptr_to_corrupt);
            if (ptr == ptr_to_corrupt) {
                xprintf("!");
                memset(ptr + self->offset + offset, 0xee, size);
            }
        } else {
            xprintf("flushCPUCache(offset=%#x+%#x, size=%#lx): weird", self->offset, offset, size);
        }
        Orig(self, offset, size);

    }
};
HOOK_DEFINE_TRAMPOLINE(StubBgUnitGroupInitSpecific) {
    static void Callback(void *self, int type) {
        Orig(self, type);
        float *fp = (float *)((char *)self + 0x6c);
        xprintf("InitSpecific(%p, %d): %f,%f,%f %f,%f", self, type, fp[0], fp[1], fp[2], fp[3], fp[4]);
        fp[3] /= 2;
        fp[4] *= 2;

    }
};

// this stretches blocks' visual appearance
HOOK_DEFINE_TRAMPOLINE(StubBgRendererXX) {
    static void Callback(void *self, int w1, float *xyz, void *w3, float *wh_in_blocks, int x5, void *x6) {
        float new_wh_in_blocks[2] = {wh_in_blocks[0] / 2, wh_in_blocks[1] * 2};
        Orig(self, w1, xyz, w3, new_wh_in_blocks, x5, x6);
    }
};

HOOK_DEFINE_TRAMPOLINE(Stub_gsys_ProcessMeter_measureBeginSystem) {
    static long Callback(void *self, struct string *name, int x2) {
        xprintf("-- measureBeginSystem(%s)", name->str);
        return Orig(self, name, x2);
    }
};

extern "C" void exl_main(void* x0, void* x1) {
    /* Setup hooking enviroment. */
    log_str("exl_main");
    exl::hook::Initialize();

    serve_main();

    // this is for 3.0.1:

    StubStatemgrSetState::InstallAtOffset(0x8b9280);
    //StubOpenFile::InstallAtOffset(0x008b7b80);
    //StubWtf::InstallAtOffset(0x1bc1590);
    //StubSearchAssetCallTableByName::InstallAtOffset(0x005ac9e0);
    //Stub_xlink2_System_setGlobalPropertyValue::InstallAtOffset(0x5a3490);
    //StubGetBlockInfo::InstallAtOffset(0x00e25ae0);

    //Stub_gsys_Model_create::InstallAtOffset(0x003e8cd0);
    //Stub_gsys_Model_pushBack::InstallAtOffset(0x003e90f0);

    //Stub_agl_VertexBuffer_flushCPUCache::InstallAtOffset(0x002fba20);
    //StubBgUnitGroupInitSpecific::InstallAtOffset(0x00daf110);
    //StubBgRendererXX::InstallAtOffset(0x00da2ec0);
    //Stub_gsys_ProcessMeter_measureBeginSystem::InstallAtOffset(0x0046c2b0);

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

    log_str("done hooking");
}

extern "C" NORETURN void exl_exception_entry() {
    /* TODO: exception handling */
    log_str("exl_exception_entry");
    EXL_ABORT(0x420);
}

// tilted blocks: p/x *(int*)($slide+0x00da3ae8 ) = 0x1e2a1000

