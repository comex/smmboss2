#include <switch.h>
#include "nxworld_main.h"
#include <vector>
#include <algorithm>
#include <string.h>

extern "C" {
    void virtmemSetup(void);
    void newlibSetup(void);
    void __libnx_init_thread(void);

    int __nx_applet_type = -2;

    void __nx_exit() {
        // shouldn't be called
        log_str("__nx_exit");
        while (1) {}
    }
}

static const SocketInitConfig socket_init_config = {
    .tcp_tx_buf_size        = 0x10000,
    .tcp_rx_buf_size        = 0x10000,
    .tcp_tx_buf_max_size    = 0x10000,
    .tcp_rx_buf_max_size    = 0x10000,

    .udp_tx_buf_size        = 256,
    .udp_rx_buf_size        = 256,

    .sb_efficiency          = 2,

    .num_bsd_sessions       = 3,
    .bsd_service_type       = BsdServiceType_User,
};

static void early_init(Handle nxworld_thread) {
    log_str("before init");
    envSetup(NULL, nxworld_thread, NULL); 
    newlibSetup();
    virtmemSetup();
    __libnx_init_thread();
    log_str("after init");
    Result rc;
    rc = smInitialize();
    if (R_FAILED(rc)) {
        log_str("smInitialize failed");
        diagAbortWithResult(rc);
    }
    rc = socketInitialize(&socket_init_config);
    if (R_FAILED(rc)) {
        log_str("socketInitialize failed");
        diagAbortWithResult(rc);
    }
    log_str("early_init done");
}

struct mem_regions {
private:
    struct region {
        uintptr_t start;
        size_t size;
        uint32_t perm;
    };
    std::vector<region> cached_regions_;
    Mutex mutex_{};

    void load_cached_regions() {
        cached_regions_.clear();

        MemoryInfo meminfo{};
        u32 pageinfo;

        do {
            Result rc = svcQueryMemory(&meminfo, &pageinfo, meminfo.addr + meminfo.size);
            if (R_FAILED(rc)) {
                log_str("svcQueryMemory failed");
                diagAbortWithResult(rc);
            }

            if (meminfo.type != MemType_Unmapped) {
                cached_regions_.push_back(region{
                    .start = meminfo.addr,
                    .size = meminfo.size,
                    .perm = meminfo.perm,
                });
            }
            if (0) {
                xprintf("addr:%lx size:%lx type:%x attr:%x perm:%x\n",
                        meminfo.addr, meminfo.size, meminfo.type, meminfo.attr, meminfo.perm);
            }
        } while(meminfo.addr + meminfo.size != 0);
    }

    const region *find_cached_region(uintptr_t addr) {
        auto it = std::lower_bound(
            cached_regions_.begin(), cached_regions_.end(), addr,
            [](const region &a, uintptr_t addr) {
                return a.start < addr;
            }
        );
        if (it != cached_regions_.end() &&
            (addr - it->start) < it->size) {
            return &*it;
        }
        return nullptr;
    }

public:
    size_t accessible_bytes_at(uintptr_t addr, bool want_write) {
        mutexLock(&mutex_);
        size_t ret = 0;
        // This assumes that mapped things are never unmapped or have their
        // protections changed, but non-mapped things might be mapped later.
        // It would be easier to just yolo the access and install an exception
        // handler, but that doesn't seem to work properly with Yuzu.
        const region *r = find_cached_region(addr);
        if (!r) {
            load_cached_regions();
            r = find_cached_region(addr);
        }
        if (r) {
            Permission needed = want_write ? Perm_Rw : Perm_R;
            if ((r->perm & needed) == needed) {
                return r->size - (addr - r->start);
            }
        }
        mutexUnlock(&mutex_);
        return ret;
    }
};

static mem_regions s_mem_regions;
static size_t safe_memcpy(void *dst, bool check_dst,
                          const void *src, bool check_src,
                          size_t size) {
    size_t orig_size = size;
    while (size > 0) {
        size_t cur_size = size;
        if (check_dst) {
            cur_size = std::min(size, s_mem_regions.accessible_bytes_at((uintptr_t)dst, true));
        }
        if (check_src) {
            cur_size = std::min(size, s_mem_regions.accessible_bytes_at((uintptr_t)src, false));
        }
        if (cur_size == 0) {
            break;
        }
        memcpy(dst, src, cur_size);
        dst = (char *)dst + cur_size;
        src = (const char *)src + cur_size;
    }
    return orig_size - size;
}

#include "mongoose.h"

enum req_type : uint8_t {
    REQ_READ = 1,
    REQ_WRITE = 2,
    REQ_SET_FILTER = 3,
};

enum resp_type : uint8_t {
    RESP_OK = 1,
    RESP_ERR = 2,
    RESP_EVENT = 3,
};


#define ALREADY_SENT_RESPONSE 255

struct req {
    uint8_t what;
    union {
        struct {
            uint64_t addr;
            uint64_t len;
        } __attribute__((packed)) rw;
    };

} __attribute__((packed));


static uint8_t handle_ws_packet(const void *buf, size_t len, struct mg_connection *c) {
    if (len < 1) {
        return 0;
    }

    return 0;
}

_Alignas(16) static char hose_buf[1048576];
static void *hose_thread(void *cast_fd) {
    int fd = (int)(uintptr_t)cast_fd;
}

static void mongoose_callback(struct mg_connection *c, int ev, void *ev_data) {
    if (ev == MG_EV_HTTP_MSG) {  // New HTTP request received
        struct mg_http_message *hm = (struct mg_http_message *) ev_data;
        if (mg_match(hm->uri, mg_str("/ws/hose"), NULL)) {
            mg_ws_upgrade(c, hm, NULL);
            mg->data[0] = 'H';
        } else if (mg_match(hm->uri, mg_str("/ws/rpc"), NULL)) {
            mg_ws_upgrade(c, hm, NULL);
            mg->data[0] = 'R';
        } else {
            mg_http_reply(c, 404, "", "not found");
        }
    } else if (ev == MG_EV_WRITE) {
        if (c->send.len == 0 && mg->data[0] == 'H') {
            // try to hand off to dedicated thread for efficiency
            pthread_attr_t attr;
            if (pthread_attr_init(&attr)) {
                goto tcfail;
            }
            if (pthread_attr_setstacksize(&attr, 0x4000)) {
                goto tcfail_attr;
            }
            pthread_t pt;
            if (pthread_create(&pt, &attr, hose_thread, c->fd) {
                goto tcfail_attr;
            }
            c->fd = nullptr;
            mg_close_conn(c);
            return;
        tcfail_attr:
            pthread_attr_destroy(&attr);
        tcfail:
            static const char err[] = "thread creation fail";
            mg_ws_send(c, err, sizeof(err), WEBSOCKET_OP_TEXT);
            c->is_draining = 1;
        }
    } else if (ev == MG_EV_WS_MSG) {
#if 0
        if (!c->fn_data) {
            c->fn_data = new 
        }
#endif
        struct mg_ws_message *wm = (struct mg_ws_message *)ev_data;
        uint8_t rc = handle_ws_packet(wm->data.buf, wm->data.len, c);
        if (rc != ALREADY_SENT_RESPONSE) {
            mg_ws_send(c, &rc, sizeof(rc), WEBSOCKET_OP_BINARY);
        }
#if 0
    } else if (ev == MG_EV_CLOSE) {
        delete (connection_info *)c->fn_data;
#endif
    }
}

static void serve() {
    struct mg_mgr mgr;
    mg_mgr_init(&mgr);
    if (!mg_http_listen(&mgr, "http://0.0.0.0:8000", mongoose_callback, NULL)) {
        log_str("mg_http_listen failed");
        diagAbortWithResult(MAKERESULT(444, 444));
    }
    if (!mg_wakeup_init(&mgr)) {
        log_str("mg_wakeup_init failed");
        diagAbortWithResult(MAKERESULT(444, 444));
    }
    while (1) {
        mg_mgr_poll(&mgr, 1000000);
    }
}

void nxworld_main(Handle nxworld_thread) {
    early_init(nxworld_thread);
    xprintf("%p\n", &safe_memcpy);
    char c;
    safe_memcpy(&c, true, &c, true, 1);
    serve();
}
