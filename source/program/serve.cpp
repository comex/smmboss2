#include "common.hpp"
#include "stuff.hpp"
#include "serve.hpp"
#include <vector>
#include <algorithm>
#include <string.h>
#include <mutex>
#include "util/modules.hpp"
#include "syscalls.h"

static void start_thread(const char *name, void *(*f)(void *), void *ctx) {
    pthread_attr_t attr;
    assert(!pthread_attr_init(&attr));
    assert(!pthread_attr_setstacksize(&attr, 0x4000));
    pthread_t pt;
    int create_ret = pthread_create(&pt, &attr, f, ctx);
    assert(!create_ret);
    int setname_ret = pthread_setname_np(pt, name);
    assert(!setname_ret);
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
        constexpr bool debug = false;

        do {
            Result rc = svcQueryMemory(&meminfo, &pageinfo, meminfo.addr + meminfo.size);
            if (R_FAILED(rc)) {
                panic("svcQueryMemory failed");
            }

            if (debug) {
                xprintf("addr:%lx size:%lx type:%x attr:%x perm:%x\n",
                        meminfo.addr, meminfo.size, meminfo.type, meminfo.attr, meminfo.perm);
            }
            if (meminfo.type == MemType_Unmapped) {
                if (debug) {
                    xprintf("    ...ignoring");
                }
                continue;
            }
            if (!cached_regions_.empty()) {
                auto &last = cached_regions_.back();
                if (last.perm == meminfo.perm &&
                    last.start + last.size == meminfo.addr) {
                    last.size += meminfo.size;
                    if (debug) {
                        xprintf("    ...absorbing");
                    }
                    continue;
                }
            }
            cached_regions_.push_back(region{
                .start = meminfo.addr,
                .size = meminfo.size,
                .perm = meminfo.perm,
            });
        } while(meminfo.addr + meminfo.size != 0);
    }

    const region *find_cached_region(uintptr_t addr) {
        auto it = std::lower_bound(
            cached_regions_.begin(), cached_regions_.end(), addr,
            [](const region &a, uintptr_t addr) {
                return a.start + a.size <= addr;
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
        size -= cur_size;
    }
    return orig_size - size;
}

#include "../../externals/mongoose/mongoose.h"

#define offsetof_end(ty, what) \
    (offsetof(ty, what) + sizeof(((ty *)0)->what))

void hose::push_fd(int fd) {
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

void hose::thread_func() {
    while (1) {
        do_iter();
    }
}

void hose::do_iter() {
    // pick up new connection if present
    int new_fd = new_fd_.exchange(-1, std::memory_order_relaxed);
    if (new_fd != -1) {
        if (cur_fd_ != -1) {
            close(cur_fd_);
        }
        cur_fd_ = new_fd;
    }

    write_info write_info = write_info_.load(std::memory_order_acquire);
    uint32_t read_offset = read_offset_.load(std::memory_order_relaxed);

    assert(read_offset <= write_info.wrap_offset &&
           write_info.write_offset <= write_info.wrap_offset &&
           write_info.wrap_offset <= sizeof(buf_));

    if (read_offset == write_info.write_offset) {
        // no data to send
        return do_sleep();
    }

    if (read_offset == write_info.wrap_offset) {
        read_offset = 0;
    }

    size_t to_send;
    if (read_offset < write_info.write_offset) {
        to_send = write_info.write_offset - read_offset;
    } else { // read_offset > write_offset
        to_send = write_info.wrap_offset - read_offset;
    }

    ssize_t actual;
    if (cur_fd_ == -1) {
        // nobody to send to; just discard data
        actual = to_send;
    } else {
        actual = send(cur_fd_, buf_ + read_offset, to_send, 0);
        if (actual == -1) {
            xprintf("send() failed: %s", strerror(errno));
            close(cur_fd_);
            cur_fd_ = -1;
            return;
        }
        if (actual == 0) {
            xprintf("send() returned 0?");
        }
    }
    read_offset += (size_t)actual;
    assert(read_offset <= sizeof(buf_));
    read_offset_.store(read_offset, std::memory_order_release);
}

void hose::do_sleep() {
    usleep(5000);
}

std::tuple<bool, hose::write_info> hose::reserve_space(size_t size, bool for_overrun) {
    assert_on_write_thread();
    uint32_t read_offset = read_offset_.load(std::memory_order_acquire);
    write_info write_info = write_info_.load(std::memory_order_relaxed);
    size_t needed_size = size + (for_overrun ? 0 : (OUTGOING_WS_HEADER_SIZE + OVERRUN_BODY_SIZE));
    if (needed_size < size) {
        needed_size = SIZE_MAX;
    }
    bool ok = false;
    if (write_info.write_offset < read_offset) {
        if (needed_size < read_offset - write_info.write_offset) { // not <=
            ok = true;
        }
    } else {
        if (needed_size <= sizeof(buf_) - write_info.write_offset) {
            ok = true;
        } else if (needed_size < read_offset) {
            ok = true;
            write_info.wrap_offset = write_info.write_offset;
            write_info.write_offset = 0;
        }
    }
    if (!ok) {
        assert(!for_overrun);
        if (!write_info.just_wrote_overrun) {
            write_overrun();
        }
        total_overrun_size_.fetch_add(size, std::memory_order_relaxed);
    }
    write_info.write_offset += size;
    write_info.just_wrote_overrun = for_overrun;
    write_info.wrap_offset = std::max(write_info.wrap_offset, write_info.write_offset);
    return std::make_tuple(ok, write_info);
}

void hose::write_overrun() {
    //xprintf("write_overrun size=%zu", size);
    write_packet([&](auto &w) {
        w.write_tag({"overrun"});
    }, /*for_overrun*/ true);
}

void hose::assert_on_write_thread() {
    // the game logic that needs to write runs on the main thread, which is
    // also the thread we are initialized on
    pthread_t self = pthread_self();
    if (self != s_main_thread) {
        panic("assert_on_write_thread failed");
    }
}

hose s_hose;

struct mongoose_server {
    void serve() {
#if MG_ENABLE_LOG
        setup_log();
#endif

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
        RPC_REQ_GET_OVERRUN_STATS = 3,
    };

    struct rpc_req {
        enum rpc_req_type type;
        union {
            struct {
                uint64_t addr;
                uint64_t len;
                char data[0];
            } __attribute__((packed)) read;
            struct {
                uint64_t addr;
                char data[0];
            } __attribute__((packed)) write;
            char get_overrun_stats[0];
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
            if (len != offsetof_end(rpc_req, read)) {
                err = "wrong len for read";
                goto err;
            }
            size_t read_len = req->read.len;
            size_t limit = 65536;
            if (c->send.size < limit) {
                if (!mg_iobuf_resize(&c->send, limit)) {
                    err = "mg_iobuf_resize failed";
                    goto err;
                }
            }
            size_t full_len = read_len + OUTGOING_WS_HEADER_SIZE;
            if (full_len < read_len || full_len > c->send.size - c->send.len) {
                err = "i'm overstuffed";
                goto err;
            }
            uint8_t *header = c->send.buf + c->send.len;
            uint8_t *past_header = header + OUTGOING_WS_HEADER_SIZE;
            size_t actual = safe_memcpy(past_header, false, (void *)req->read.addr, true, read_len);
            fill_ws_header(header, read_len);
            c->send.len += past_header + actual - header;
            return;
        }

        case RPC_REQ_WRITE: {
            if (len < offsetof_end(rpc_req, write)) {
                err = "too short for write";
                goto err;
            }
            size_t write_len = len - offsetof_end(rpc_req, write);
            size_t actual = safe_memcpy((void *)req->write.addr, true, req->write.data, false, write_len);
            mg_ws_send(c, &actual, sizeof(actual), WEBSOCKET_OP_BINARY);
            return;
        }

        case RPC_REQ_GET_OVERRUN_STATS: {
            if (len != offsetof_end(rpc_req, get_overrun_stats)) {
                err = "wrong len for get_overrun_stats";
                goto err;
            }
            uint64_t val = s_hose.get_and_reset_total_overrun_size();
            mg_ws_send(c, &val, sizeof(val), WEBSOCKET_OP_BINARY);
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
                send_hello(c);
            }
        }
    }
    void send_hello(struct mg_connection *c) {
        std::vector<uint64_t> info;
        for (int i = 0; i < exl::util::mem_layout::s_ModuleCount; i++) {
            const exl::util::ModuleInfo &mod_info = exl::util::GetModuleInfo(i);
            auto do_range = [&](exl::util::Range range) {
                info.push_back(range.m_Start);
                info.push_back(range.m_Size);
            };
            do_range(mod_info.m_Total);
            do_range(mod_info.m_Text);
            do_range(mod_info.m_Rodata);
            do_range(mod_info.m_Data);
        }
        mg_ws_send(c, info.data(), info.size() * sizeof(info[0]), WEBSOCKET_OP_BINARY);
    }

    void setup_log() {
        mg_log_set(MG_LL_DEBUG);
        mg_log_set_fn([](char c, void *) {
            static std::string line;
            if (c == '\n') {
                log_str(line.c_str());
                line.clear();
            } else {
                line.push_back(c);
            }
        }, nullptr);
    }
};

static mongoose_server s_mongoose_server;

static char s_socket_heap[0x600000] alignas(0x1000);
void serve_main() {
    int e = nnsocketInitialize(s_socket_heap, sizeof(s_socket_heap), 0x20000, 0xe);
    if (e) {
        panic("nnsocketInitialize failed: %x", e);
    }
    s_main_thread = pthread_self();
    start_thread("hose", [](void *ignored) -> void * {
        s_hose.thread_func();
        return nullptr;
    }, nullptr);
    start_thread("mongoose", [](void *ignored) -> void * {
        s_mongoose_server.serve();
        return nullptr;
    }, nullptr);
}
