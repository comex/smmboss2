#pragma once
#include <atomic>
#include <pthread.h>
#include <tuple>
#include <string.h>

size_t add_ws_header_size(size_t size);
uint8_t *fill_ws_header(uint8_t *p, size_t size, size_t min_size);

struct hose_tag {
    char name[8];
};

struct hose {
    void push_fd(int fd);

    // reader thread func:
    void thread_func();

    // writer thread func:
    template <typename F>
    void write_packet(size_t size, F &&writeout, bool for_overrun = false) {
        size_t full_size = add_ws_header_size(size);
        write_raw(full_size, [&](uint8_t *p) {
            uint8_t *past_header = fill_ws_header(p, size, size);
            writeout(past_header);
        }, for_overrun);
    }

    template <typename ...T>
    void write_fixed(const T &...t) {
        size_t total_size = 0;
        ((total_size += sizeof(t)), ...);
        write_packet(total_size, [&](uint8_t *dst) {
            ((memcpy(dst, &t, sizeof(t)), dst += sizeof(t)), ...);
        });
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
    std::atomic<write_info> write_info_{{.wrap_offset = sizeof(buf_)}};
    std::atomic<uint32_t> read_offset_{0};
    _Alignas(16) uint8_t buf_[128 * 1024];

    // reader thread data:
    int cur_fd_{-1};

    // reader thread funcs:
    void do_iter();
    void do_sleep();

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

    std::tuple<bool, write_info> reserve_space(size_t size, bool for_overrun);

    void write_overrun(size_t size);

    void assert_on_write_thread();
};

extern hose s_hose;

void serve_main();
