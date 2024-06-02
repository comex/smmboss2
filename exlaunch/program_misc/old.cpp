
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
