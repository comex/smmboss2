#include "common.hpp"
#include "stuff.hpp"
#include <vector>
#include <algorithm>
#include <string.h>
#include <pthread.h>
#include <mutex>
#include "util/modules.hpp"

static void start_thread(void *(*f)(void *), void *ctx) {
    pthread_attr_t attr;
    assert(!pthread_attr_init(&attr));
    assert(!pthread_attr_setstacksize(&attr, 0x4000));
    pthread_t pt;
    int create_ret = pthread_create(&pt, &attr, f, ctx);
    assert(!create_ret);
}

static pthread_t s_main_thread;

struct mem_regions {
private:
    struct region {
        uintptr_t start;
        size_t size;
        uint32_t perm;
    };
    std::vector<region> cached_regions_;
    std::mutex mutex_;

    void load_cached_regions() {
        cached_regions_.clear();

        MemoryInfo meminfo{};
        u32 pageinfo;

        do {
            Result rc = svcQueryMemory(&meminfo, &pageinfo, meminfo.addr + meminfo.size);
            if (R_FAILED(rc)) {
                panic("svcQueryMemory failed");
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

#include "../../externals/mongoose/mongoose.h"

#define offsetof_end(ty, what) \
    (offsetof(ty, what) + sizeof(((ty *)0)->what))

constexpr static size_t add_ws_header_size(size_t size) {
    size_t header_size;
    if (size < 126) {
        header_size = 2;
    } else if (size < 65536) {
        header_size = 4;
    } else {
        header_size = 10;
    }
    size_t full_size = size + header_size;
    if (full_size < size) {
        return SIZE_MAX;
    }
    return full_size;
}

static uint8_t *fill_ws_header(uint8_t *p, size_t size, size_t min_size) {
    // fill out websocket frame header.  based on mkhdr.
    assert(size >= min_size);
    p[0] = WEBSOCKET_OP_BINARY | 128;
    if (min_size < 126) {
        p[1] = (uint8_t)size;
        return p + 2;
    } else if (min_size < 65536) {
        p[1] = 126;
        p[2] = (uint8_t)(size >> 8);
        p[3] = (uint8_t)(size >> 0);
        return p + 4;
    } else {
        p[1] = 127;
        uint64_t swapped = __builtin_bswap64(size);
        memcpy(&p[2], &swapped, 8);
        return p + 10;
    }
}


__attribute__((constructor))
static void ctor_test() {
    log_str("i should be kept");
}

struct hose {
    void push_fd(int fd) {
        xprintf("hose::push_fd(%d)", fd);
        // disable nonblock
        int fl = fcntl(fd, F_GETFL, 0);
        assert(fl != -1);
        int ret = fcntl(fd, F_SETFL, fl & ~O_NONBLOCK);
        assert(ret != -1);

        // send to thread
        int old = new_fd_.exchange(fd, std::memory_order_relaxed);
        if (old != -1) {
            close(old);
        }
    }

    // reader thread func:
    void thread_func() {
        while (1) {
            do_iter();
        }
    }

    // writer thread func:
    template <typename F>
    void write_packet(size_t size, F &&writeout, bool for_overrun = false) {
        size_t full_size = add_ws_header_size(size);
        write_raw(full_size, [&](uint8_t *p) {
            uint8_t *past_header = fill_ws_header(p, size, size);
            writeout(past_header);
        }, for_overrun);
    }

private:
    // protocol:
    enum hose_packet_type : uint8_t {
        HOSE_PACKET_OVERRUN = 1,
    };

    // shared data:
    struct write_info {
        uint32_t write_offset;
        uint32_t wrap_offset:31,
                 just_wrote_overrun:1;
    };
    static_assert(sizeof(write_info) == 8);
    static_assert(std::atomic<write_info>::is_always_lock_free);

    std::atomic<int> new_fd_{-1};
    std::atomic<write_info> write_info_{};
    std::atomic<uint32_t> read_offset_{0};
    _Alignas(16) uint8_t buf_[128 * 1024];

    // reader thread data:
    int cur_fd_{-1};

    // reader thread funcs:
    void do_iter() {
        // pick up new connection if present
        int new_fd = new_fd_.exchange(0, std::memory_order_relaxed);
        if (new_fd != -1) {
            if (cur_fd_ != -1) {
                close(cur_fd_);
            }
            cur_fd_ = new_fd;
        }

        write_info write_info = write_info_.load(std::memory_order_acquire);
        uint32_t read_offset = read_offset_.load(std::memory_order_relaxed);

        assert(read_offset <= sizeof(buf_) &&
               write_info.write_offset <= sizeof(buf_));
        if (write_info.write_offset < read_offset) {
            assert(write_info.wrap_offset >= read_offset &&
                   write_info.wrap_offset <= sizeof(buf_));
        }

        if (cur_fd_ == -1) {
            // nobody to send to
            read_offset_.store(write_info.write_offset, std::memory_order_relaxed);
            return do_sleep();
        }

        size_t to_send;
        if (read_offset == write_info.write_offset) {
            // no data to send
            return do_sleep();
        } else if (read_offset < write_info.write_offset) {
            to_send = write_info.write_offset - read_offset;
        } else { // read_offset > write_offset
            to_send = write_info.wrap_offset - read_offset;
        }

        ssize_t ret = send(cur_fd_, buf_ + read_offset, to_send, 0);
        if (ret == -1) {
            xprintf("send() failed: %s", strerror(errno));
            close(cur_fd_);
            cur_fd_ = -1;
            return;
        }
        if (ret == 0) {
            xprintf("send() returned 0?");
        }
        read_offset += (size_t)ret;
        assert(read_offset <= sizeof(buf_));
        if (read_offset == sizeof(buf_)) {
            read_offset = 0;
        }
        read_offset_.store(read_offset, std::memory_order_release);
    }
    void do_sleep() {
        usleep(5000);
    }

    static constexpr size_t OVERRUN_BODY_SIZE = 9;

    // writer thread funcs:
    template <typename F>
    void write_raw(size_t size, F &&writeout, bool for_overrun) {
        auto [ok, new_write_info] = reserve_space(size, for_overrun);
        if (!ok) {
            return;
        }
        writeout(buf_ + (new_write_info.write_offset - size));
        write_info_.store(new_write_info, std::memory_order_release);
    }

    std::tuple<bool, write_info> reserve_space(size_t size, bool for_overrun) {
        assert_on_write_thread();
        uint32_t read_offset = read_offset_.load(std::memory_order_acquire);
        write_info write_info = write_info_.load(std::memory_order_relaxed);
        size_t needed_size = size + (for_overrun ? 0 : add_ws_header_size(OVERRUN_BODY_SIZE));
        if (needed_size < size) {
            needed_size = SIZE_MAX;
        }
        bool ok = false;
        if (write_info.write_offset < read_offset) {
            if (needed_size < read_offset - write_info.write_offset) { // not <=
                ok = true;
                write_info.write_offset += size;
                write_info.just_wrote_overrun = for_overrun;
            }
        } else {
            if (needed_size <= sizeof(buf_) - write_info.write_offset) {
                ok = true;
                write_info.write_offset += size;
                write_info.just_wrote_overrun = for_overrun;
            } else if (needed_size < read_offset) {
                ok = true;
                write_info.wrap_offset = write_info.write_offset;
                write_info.write_offset = size;
                write_info.just_wrote_overrun = for_overrun;
            }
        }
        if (!ok) {
            assert(!for_overrun);
            if (!write_info.just_wrote_overrun) {
                write_overrun(size);
            }
        }
        return std::make_tuple(ok, write_info);
    }

    void write_overrun(size_t size) {
        xprintf("write_overrun size=%zu", size);
        write_packet(OVERRUN_BODY_SIZE, [&](uint8_t *p) {
            p[0] = HOSE_PACKET_OVERRUN;
            static_assert(sizeof(size) == 8);
            memcpy(p + 1, &size, 8);
        }, /*for_overrun*/ true);
    }

    void assert_on_write_thread() {
        // the game logic that needs to write runs on the main thread, which is
        // also the thread we are initialized on
        pthread_t self = pthread_self();
        if (self != s_main_thread) {
            panic("assert_on_write_thread failed");
        }
    }
};

static hose s_hose;

struct mongoose_server {
    void serve() {
        struct mg_mgr mgr;
        mg_mgr_init(&mgr);
        if (!mg_http_listen(&mgr, "http://0.0.0.0:8000", mongoose_callback, this)) {
            panic("mg_http_listen failed");
        }
        if (!mg_wakeup_init(&mgr)) {
            panic("mg_wakeup_init failed");
        }
        while (1) {
            mg_mgr_poll(&mgr, 1000000);
        }
    }

private:
    enum conn_state : uint8_t {
        CONN_STATE_DEFAULT = 0, // mongoose zeroes out data by default
        CONN_STATE_RPC_WEBSOCKET,
        CONN_STATE_DRAINING_BEFORE_HOSE_HANDOFF,
    };

    struct __attribute__((may_alias)) conn_data {
        conn_state state;
        // no more state for now
    };

    // protocol:
    enum rpc_req_type : uint8_t {
        RPC_REQ_READ = 1,
        RPC_REQ_WRITE = 2,
    };

    struct rpc_hello_resp {
        uint64_t target_start;
    } __attribute__((packed));

    struct rpc_req {
        enum rpc_req_type type;
        union {
            struct {
                uint64_t addr;
                uint64_t len;
                char data[0];
            } __attribute__((packed)) rw;
        };

    } __attribute__((packed));
    // end protocol

    void handle_rpc_packet(const void *buf, size_t len, struct mg_connection *c) {
        auto req = (struct rpc_req *)buf;
        const char *err;
        if (len < offsetof_end(rpc_req, type)) {
            err = "too short for type";
            goto err;
        }
        switch (req->type) {
        case RPC_REQ_READ: {
            if (len < offsetof_end(rpc_req, rw)) {
                err = "too short for rw";
                goto err;
            }
            size_t rw_len = req->rw.len;
            size_t limit = 65536;
            if (c->send.size < limit) {
                if (!mg_iobuf_resize(&c->send, limit)) {
                    err = "mg_iobuf_resize failed";
                    goto err;
                }
            }
            size_t full_len = add_ws_header_size(rw_len);
            if (full_len > c->send.size - c->send.len) {
                err = "i'm overstuffed";
                goto err;
            }
            uint8_t *header = c->send.buf + c->send.len;
            uint8_t *past_header = header + (full_len - rw_len);
            size_t actual = safe_memcpy(past_header, false, (void *)req->rw.addr, true, rw_len);
            uint8_t *ph = fill_ws_header(header, actual, rw_len);
            assert(ph == past_header);
            c->send.len += ph + actual - header;
            return;
        }

        case RPC_REQ_WRITE: {
            if (len < offsetof_end(rpc_req, rw)) {
                err = "too short for rw";
                goto err;
            }
            if (offsetof_end(rpc_req, rw) - len < req->rw.len) {
                err = "too short for data";
                goto err;
            }
            size_t actual = safe_memcpy((void *)req->rw.addr, true, req->rw.data, false, req->rw.len);
            mg_ws_send(c, &actual, sizeof(actual), WEBSOCKET_OP_BINARY);
            return;
        }

        default:
            err = "unknown req type";
            goto err;
        }

    err:
        mg_ws_send(c, err, strlen(err), WEBSOCKET_OP_TEXT);
        c->is_draining = 1;
    }

    static_assert(sizeof(conn_data) <= MG_DATA_SIZE);

    static void mongoose_callback(struct mg_connection *c, int ev, void *ev_data) {
        auto self = (mongoose_server *)c->fn_data;
        self->mongoose_callback_impl(c, ev, ev_data);
    }
    void mongoose_callback_impl(struct mg_connection *c, int ev, void *ev_data) {
        auto cd = (conn_data *)c->data;
        if (ev == MG_EV_HTTP_MSG) {  // New HTTP request received
            struct mg_http_message *hm = (struct mg_http_message *) ev_data;
            if (mg_match(hm->uri, mg_str("/ws/hose"), NULL)) {
                cd->state = CONN_STATE_DRAINING_BEFORE_HOSE_HANDOFF;
                mg_ws_upgrade(c, hm, NULL);
            } else if (mg_match(hm->uri, mg_str("/ws/rpc"), NULL)) {
                cd->state = CONN_STATE_RPC_WEBSOCKET;
                mg_ws_upgrade(c, hm, NULL);
            } else {
                mg_http_reply(c, 404, "", "not found");
            }
        } else if (ev == MG_EV_WRITE) {
            if (cd->state == CONN_STATE_DRAINING_BEFORE_HOSE_HANDOFF &&
                c->send.len == 0) {
                s_hose.push_fd((int)(uintptr_t)c->fd);
                c->fd = nullptr;
                mg_close_conn(c);
                return;
            }
        } else if (ev == MG_EV_WS_MSG) {
            if (cd->state != CONN_STATE_RPC_WEBSOCKET) {
                // did we get a websocket message from a hose connection?
                c->is_draining = 1;
                return;
            }
            struct mg_ws_message *wm = (struct mg_ws_message *)ev_data;
            handle_rpc_packet(wm->data.buf, wm->data.len, c);
        } else if (ev == MG_EV_WS_OPEN) {
            if (cd->state == CONN_STATE_RPC_WEBSOCKET) {
                rpc_hello_resp hello = {
                    .target_start = exl::util::modules::GetTargetStart(),
                };
                mg_ws_send(c, &hello, sizeof(hello), WEBSOCKET_OP_BINARY);
            }
        }
    }
};

static mongoose_server s_mongoose_server;

void serve_main() {
    s_main_thread = pthread_self();
    start_thread([](void *ignored) -> void * {
        s_hose.thread_func();
        return nullptr;
    }, nullptr);
    start_thread([](void *ignored) -> void * {
        s_mongoose_server.serve();
        return nullptr;
    }, nullptr);
}
