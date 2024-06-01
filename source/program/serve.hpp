#pragma once
#include <atomic>
#include <pthread.h>
#include <tuple>
#include <string.h>
#include "stuff.hpp"

constexpr size_t OUTGOING_WS_HEADER_SIZE = 10;

static inline void fill_ws_header(uint8_t *p, size_t size) {
    p[0] = 0x82;
    p[1] = 0x7f;
    uint64_t swapped = __builtin_bswap64(size);
    memcpy(&p[2], &swapped, 8);
}

struct hose {
    void push_fd(int fd);

    // reader thread func:
    void thread_func();

    template <typename self_t>
    struct writer_base {
        template <typename T>
        void write_prim(const T &t) {
            ((self_t *)this)->write_range(&t, &t + 1);
        }
        struct tag { char c[8]; };
        void write_tag(tag t) {
            write_prim(t);
        }
    };

    struct size_calculator : public writer_base<size_calculator> {
        size_t size_;
        void write_range(const void *start, const void *end) {
            size_t old_size = size_;
            size_t new_size = old_size + ((uint8_t *)end - (uint8_t *)start);
            size_ = new_size > old_size ? new_size : SIZE_MAX;
        }
    };

    struct actual_writer : public writer_base<actual_writer> {
        uint8_t *cur_ptr_;
        void write_range(const void *start, const void *end) {
            size_t size = (uint8_t *)end - (uint8_t *)start;
            memcpy(cur_ptr_, start, size);
            cur_ptr_ += size;
        }
    };

    // writer thread func:
    void write_packet(auto &&callback, bool for_overrun = false) {
        size_calculator sc{.size_ = OUTGOING_WS_HEADER_SIZE};
        callback(sc);
        size_t size = sc.size_;
        auto [ok, new_write_info] = reserve_space(size, for_overrun);
        if (!ok) {
            return;
        }
        uint8_t *ptr = buf_ + (new_write_info.write_offset - size);
        fill_ws_header(ptr, size - OUTGOING_WS_HEADER_SIZE);
        actual_writer aw{.cur_ptr_ = ptr + OUTGOING_WS_HEADER_SIZE};
        callback(aw);
        assert(aw.cur_ptr_ == ptr + size);

        write_info_.store(new_write_info, std::memory_order_release);
    }

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
    _Alignas(16) uint8_t buf_[128 * 1024];

    // reader thread data:
    int cur_fd_{-1};

    // reader thread funcs:
    void do_iter();
    void do_sleep();

    static constexpr size_t OVERRUN_BODY_SIZE = 16;

    std::tuple<bool, write_info> reserve_space(size_t size, bool for_overrun);

    void write_overrun(size_t size);

    void assert_on_write_thread();
};

extern hose s_hose;

void serve_main();
