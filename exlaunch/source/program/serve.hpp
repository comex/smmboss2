#pragma once
#include <atomic>
#include <pthread.h>
#include <tuple>
#include <string.h>
#include "stuff.hpp"

#include "nn/os/os_condition_variable_common.hpp"
#include "nn/os/os_event_types.hpp"
#include "nn/os/os_event_api.hpp"

constexpr size_t OUTGOING_WS_HEADER_SIZE = 10;

size_t add_ws_header_size(size_t size);
uint8_t *fill_ws_header(uint8_t *p, size_t size);

// TODO: figure out a capitalization scheme ffs
template <typename self_t>
struct writer_base {
    template <typename T>
    void write_prim(const T &t) {
        write_n(&t, 1);
    }
    void write_range(const void *start, const void *end) {
        ((self_t *)this)->write_raw(start, (char *)end - (char *)start);
    }
    template <typename T>
    void write_n(const T *ts, size_t n) {
        ((self_t *)this)->write_raw(ts, n * pt_size_of<T>);
    }
    template <typename T>
    void write_n(pt_pointer<T> ts, size_t n) {
        ((self_t *)this)->write_raw(ts.raw, n * pt_size_of<T>);
    }
    void write_tag(tag8 t) {
        write_prim(t);
    }
};

struct size_calculator : public writer_base<size_calculator> {
    size_t size_;
    void write_raw(const void *ptr, size_t write_size) {
        size_t old_size = size_;
        size_t new_size = old_size + write_size;
        size_ = new_size > old_size ? new_size : SIZE_MAX;
    }
};

struct actual_writer : public writer_base<actual_writer> {
    uint8_t *cur_ptr_;
    void write_raw(const void *ptr, size_t size) {
        memcpy(cur_ptr_, ptr, size);
        cur_ptr_ += size;
    }
};

struct hose {
    static constexpr auto DEFAULT_REVIEW_CALLBACK = [](uint8_t *ptr, size_t size) -> bool {
        return true;
    };

    void init();

    void push_fd(int fd);

    // reader thread func:
    void thread_func();

    // writer thread func:
    void write_packet(auto &&write_callback) {
        write_packet(write_callback, DEFAULT_REVIEW_CALLBACK, false);
    }
    void write_packet(
        auto &&write_callback,
        auto &&review_callback,
        bool for_overrun = false
    ) {
        size_calculator sc{.size_ = 0};
        write_callback(sc);
        size_t size = sc.size_;
        auto [ok, new_write_info] = reserve_space_and_write_header(size, for_overrun);
        if (!ok) {
            return;
        }
        uint8_t *ptr = buf_ + (new_write_info.write_offset - size);
        actual_writer aw{.cur_ptr_ = ptr};
        write_callback(aw);
        assert(aw.cur_ptr_ == ptr + size);
        if (!review_callback(ptr, size)) {
            return;
        }

        write_info_.store(new_write_info, std::memory_order_release);
        total_written_bytes_.fetch_add(size, std::memory_order_relaxed);

    }

    // callable from any thread
    void set_enable_backpressure(bool enable);
    void assert_on_write_thread();

    // any thread, used for stats:
    std::atomic<uint64_t> total_overrun_bytes_{0};
    std::atomic<uint64_t> total_written_bytes_{0};
    std::atomic<uint64_t> backpressured_nsec_{0};

private:
    // shared data:
    struct write_info {
        uint32_t write_offset;
        uint32_t wrap_offset:31,
                 just_wrote_overrun:1;
    };
    static_assert(sizeof(write_info) == 8);
    static_assert(std::atomic<write_info>::is_always_lock_free);

    std::atomic<int> new_fd_{-1};
    std::atomic<write_info> write_info_{{.wrap_offset = sizeof(buf_)}};
    std::atomic<uint32_t> read_offset_{0};

    nn::os::EventType sent_event_;

    std::atomic<bool> enable_backpressure_{false};

    _Alignas(16) uint8_t buf_[2 * 1024 * 1024];

    // reader thread data:
    int cur_fd_{-1};

    // reader thread funcs:
    void do_iter();
    void do_sleep();

    static constexpr size_t OVERRUN_PACKET_SIZE = 10; // 2-byte header + 8-byte body

    std::tuple<bool, write_info> reserve_space_and_write_header(size_t size, bool for_overrun);

    bool backpressure();

    void write_overrun();
};

extern hose s_hose;

void serve_main();

enum rpc_flags : uint64_t {
    RPC_FLAG_BACKPRESSURE = 1,
    RPC_FLAG_SEND_COLLS = 2,
    RPC_FLAG_SEND_BG_EVENTS = 4,
};
extern std::atomic<uint64_t> g_cur_rpc_flags;
static inline bool test_and_clear_rpc_flag(uint64_t flag) {
    uint64_t flags = g_cur_rpc_flags.load(std::memory_order_relaxed);
    bool ret = (flags & flag) == flag;
    if (ret) {
        flags = g_cur_rpc_flags.fetch_and(~flag, std::memory_order_acq_rel);
        ret = (flags & flag) == flag;
    }
    return ret;
}

static inline bool test_rpc_flag(uint64_t flag) {
    uint64_t flags = g_cur_rpc_flags.load(std::memory_order_acquire);
    return (flags & flag) == flag;
}

