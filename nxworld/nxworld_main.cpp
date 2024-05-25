#include <switch.h>
#include "nxworld_main.h"
#include <vector>
#include <algorithm>
#include <string.h>
#include <pthread.h>
#include <mutex>

#undef assert

#define panic(...) do { \
    xprintf(__VA_ARGS__); \
    diagAbortWithResult(MAKERESULT(444, 444)); \
} while (0)

#define assert(expr) do { \
    if (!(expr)) { \
        panic("assertion failed: %s", #expr); \
    } \
} while (0)

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

static const SocketInitConfig s_socket_init_config = {
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
    rc = socketInitialize(&s_socket_init_config);
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
        std::lock_guard lk(mutex_);
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
        return 0;
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

enum rpc_req_type : uint8_t {
    RPC_REQ_READ = 1,
    RPC_REQ_WRITE = 2,
};

struct rpc_req {
    enum rpc_req_type type;
    union {
        struct {
            uint64_t addr;
            uint64_t len;
        } __attribute__((packed)) rw;
    };

} __attribute__((packed));

#define offsetof_end(ty, what) \
    (offsetof(ty, what) + sizeof(((ty *)0)->what))

static void handle_rpc_packet(const void *buf, size_t len, struct mg_connection *c) {
    auto req = (struct rpc_req *)buf;
    const char *err;
    if (len < offsetof_end(rpc_req, type)) {
        err = "too short for type";
        goto err;
    }
    switch (req->type) {
    case RPC_REQ_READ:
        // ...
        return;
    case RPC_REQ_WRITE:
        // ...
        return;
    default:
        err = "unknown req type";
        goto err;
    }

err:
    mg_ws_send(c, err, strlen(err), WEBSOCKET_OP_TEXT);
    c->is_draining = 1;
}

enum conn_state : uint8_t {
    CONN_STATE_DEFAULT = 0, // mongoose zeroes out data by default
    CONN_STATE_RPC_WEBSOCKET,
    CONN_STATE_DRAINING_BEFORE_HOSE_HANDOFF,
};

__attribute__((constructor))
static void ctor_test() {
    log_str("i should be kept");
}

struct __attribute__((may_alias)) conn_data {
    conn_state state;
    union {
        struct {
            uint64_t start_off;
        } draining_before_hose_handoff;

    };
};

static_assert(sizeof(conn_data) <= MG_DATA_SIZE);

struct hose {
    void send_fd(int fd) {
        // disable nonblock
        int fl = fcntl(fd, F_GETFL, 0);
        assert(fl != -1);
        int ret = fcntl(fd, F_SETFL, fl & ~O_NONBLOCK);
        assert(ret != -1);

        // send to thread
        int old = new_fd_.exchange(fd, memory_order_relaxed);
        if (old != -1) {
            close(old);
        }
    }
    void start_thread() {
        pthread_attr_t attr;
        assert(!pthread_attr_init(&attr));
        assert(!pthread_attr_setstacksize(&attr, 0x4000));
        pthread_t pt;
        int create_ret = pthread_create(&pt, &attr, [](void *self) -> void * {
            ((hose *)self)->thread_func();
            return nullptr;
        }, this);
        assert(!create_ret);
    }

private:
    // shared data:
    atomic<int> new_fd_{-1};
    atomic<uint64_t> write_offset_{0};
    atomic<uint64_t> read_offset_{0};
    _Alignas(16) char buf_[128 * 1024];

    // thread-private data:
    int cur_fd_{-1};

    void thread_func() {
        while (1) {
            do_iter();
        }
    }
    void do_iter() {
        // pick up new connection if present
        int new_fd = new_fd_.exchange(0, memory_order_relaxed);
        if (new_fd != -1) {
            if (cur_fd_ != -1) {
                close(cur_fd_);
            }
            cur_fd_ = new_fd;
        }

        uint64_t write_offset = write_offset_.load(memory_order_acquire);
        uint64_t read_offset = read_offset_.load(memory_order_relaxed);

        assert(read_offset <= write_offset &&
               write_offset - read_offset <= sizeof(buf));

        if (read_offset == write_offset) {
            // no data to send
            return do_sleep();
        }

        if (cur_fd_ == -1) {
            // nobody to send to
            read_offset_.store(write_offset, memory_order_relaxed);
            return do_sleep();
        }

        size_t to_send = std::min(write_offset - read_offset,
                                  sizeof(buf_) - (read_offset % sizeof(buf_)));

        ssize_t ret = send(cur_fd_,buf_ + (read_offset % sizeof(buf_)), to_send);
        if (ret == -1) {
            xprintf("send() failed: %s", strerror(errno));
            close(cur_fd_);
            cur_fd_ = -1;
            return;
        }
        if (ret == 0) {
            xprintf("send() returned 0?");
        }
        read_offset_.store(read_offset + ret);

    }
    void do_sleep() {
        usleep(5000);
    }
}

static void mongoose_callback(struct mg_connection *c, int ev, void *ev_data) {
    auto cd = (conn_data *)c->data;
    if (ev == MG_EV_HTTP_MSG) {  // New HTTP request received
        struct mg_http_message *hm = (struct mg_http_message *) ev_data;
        struct mg_str hose_start_off_s;
        uint64_t hose_start_off;
        if (mg_match(hm->uri, mg_str("/ws/hose/*"), &hose_start_off_s) &&
            mg_str_to_num(hose_start_off_s, 10, &hose_start_off, sizeof(hose_start_off))) {
            mg_ws_upgrade(c, hm, NULL);
            cd->state = CONN_STATE_DRAINING_BEFORE_HOSE_HANDOFF;
            cd->draining_before_hose_handoff.start_off = hose_start_off;
        } else if (mg_match(hm->uri, mg_str("/ws/rpc"), NULL)) {
            mg_ws_upgrade(c, hm, NULL);
            cd->state = CONN_STATE_RPC_WEBSOCKET;
        } else {
            mg_http_reply(c, 404, "", "not found");
        }
    } else if (ev == MG_EV_WRITE) {
        if (cd->state == CONN_STATE_DRAINING_BEFORE_HOSE_HANDOFF &&
            c->send.len == 0) {
            // try to hand off to dedicated thread, because mongoose is kind of
            // inefficient for sending large amounts of data
            XXX
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
        if (cd->state != CONN_STATE_RPC_WEBSOCKET) {
            // did we get a websocket message from a hose connection?
            c->is_draining = 1;
            return;
        }
        struct mg_ws_message *wm = (struct mg_ws_message *)ev_data;
        handle_rpc_packet(wm->data.buf, wm->data.len, c);
    }
}

static void serve() {
    struct mg_mgr mgr;
    mg_mgr_init(&mgr);
    if (!mg_http_listen(&mgr, "http://0.0.0.0:8000", mongoose_callback, NULL)) {
        panic("mg_http_listen failed");
    }
    if (!mg_wakeup_init(&mgr)) {
        panic("mg_wakeup_init failed");
    }
    while (1) {
        mg_mgr_poll(&mgr, 1000000);
    }
}

static void make_hose_thread() {
}

void nxworld_main(Handle nxworld_thread) {
    early_init(nxworld_thread);
    make_hose_thread();
    serve();
}
