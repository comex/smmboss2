#ifndef FUZZ_TEST
#pragma once
#endif

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#ifdef FUZZ_TEST
#include <assert.h>
#else
#include "stuff.hpp"
#endif

#include <utility>
#include <type_traits>

template <typename T>
struct __attribute__((packed)) Packed {
    T val_;
    operator T() const {
        return val_;
    }
    Packed &operator=(const T &t) {
        val_ = t;
        return *this;
    }
};

struct UInt48 {
    uint8_t bytes[6];
    UInt48() : bytes{{}} {}
    UInt48(const uint64_t &val) {
        assert(val >> 48 == 0);
        memcpy(bytes, &val, sizeof(bytes));
    }
    explicit UInt48(const void *ptr) : UInt48((uint64_t)(uintptr_t)ptr) {}
    operator uint64_t() const {
        uint64_t val = 0;
        memcpy(&val, bytes, sizeof(bytes));
        return val;
    }
    template <typename T>
    explicit operator T *() const {
        return (T *)(uintptr_t)((uint64_t)*this);
    }
};

struct Nothing {};

template <typename KeyTy, typename ValTy, size_t NUM_HASH_SLOTS, size_t NUM_ENTRY_SLOTS = NUM_HASH_SLOTS>
struct StupidHash {
    using EntryIdx = uint16_t;
    static_assert(NUM_ENTRY_SLOTS < (1 << (8 * sizeof(EntryIdx))));

    struct EntryWithVal {
        Packed<EntryIdx> prev, next;
        KeyTy key;
        ValTy value;
    };

    struct EntryNoVal {
        Packed<EntryIdx> prev, next;
        KeyTy key;
        static const ValTy value;
    };

    using Entry = std::conditional_t<std::is_same_v<ValTy, Nothing>, EntryNoVal, EntryWithVal>;

    std::pair<Entry *, bool> lookup(KeyTy key, bool insert) {
        Packed<EntryIdx> *next_p = &by_hash_[hash(key)], idx = *next_p;
        EntryIdx expected_prev = 0;
        Entry *entry;
        while (idx != 0) {
            entry = entry_at_idx(idx);
            assert(entry->prev == expected_prev);
            if (entry->key == key) {
                return std::make_pair(entry, true);
            }
            expected_prev = idx;
            next_p = &entry->next;
            idx = *next_p;
        }
        if (insert) {
            EntryIdx new_idx = try_alloc_entry_idx();
            if (!new_idx) {
                entry = nullptr;
            } else {
                *next_p = new_idx;
                entry = entry_at_idx(new_idx);
                entry->prev = expected_prev;
                entry->next = 0;
                entry->key = key;
            }
        } else {
            entry = nullptr;
        }
        return std::make_pair(entry, false);
    }

    void remove(Entry *entry) {
        EntryIdx entry_idx = entry_to_idx(entry);
        unlink_entry(entry_idx);
        assert(entries_count_ > 0);
        EntryIdx victim_idx = (EntryIdx)entries_count_--;
        if (victim_idx != entry_idx) {
            move_entry(victim_idx, entry_idx);
        }
    }

    void clear() {
        memset(&by_hash_, 0, sizeof(by_hash_));
        entries_count_ = 0;
    }

    Entry *begin() { return entries_; }
    Entry *end() { return entries_ + entries_count_; }

    size_t size() const { return entries_count_; }
    size_t capacity() const { return NUM_ENTRY_SLOTS; }
private:
    Packed<EntryIdx> *prevs_next(Entry *entry) {
        if (entry->prev == 0) {
            return &by_hash_[hash(entry->key)];
        } else {
            return &entry_at_idx(entry->prev)->next;
        }
    }

    Packed<EntryIdx> *nexts_prev(Entry *entry) {
        if (entry->next == 0) {
            return nullptr;
        } else {
            return &entry_at_idx(entry->next)->prev;
        }
    }

    void move_entry(EntryIdx old_idx, EntryIdx new_idx) {
        Entry *old_entry = entry_at_idx(old_idx);
        Entry *new_entry = entry_at_idx(new_idx);
        *new_entry = *old_entry;

        *prevs_next(old_entry) = new_idx;
        if (Packed<EntryIdx> *p = nexts_prev(old_entry)) {
            *p = new_idx;
        }
    }

    void unlink_entry(EntryIdx entry_idx) {
        Entry *entry = entry_at_idx(entry_idx);
        EntryIdx prev = entry->prev, next = entry->next;

        *prevs_next(entry) = next;
        if (Packed<EntryIdx> *p = nexts_prev(entry)) {
            *p = prev;
        }
    }

    EntryIdx entry_to_idx(Entry *entry) {
        size_t offset = (uintptr_t)entry - (uintptr_t)entries_;
        assert(offset < sizeof(entries_) && offset % sizeof(Entry) == 0);
        return offset / sizeof(Entry) + 1;
    }

    Entry *entry_at_idx(EntryIdx idx) {
        assert(idx > 0 && idx <= NUM_ENTRY_SLOTS);
        return &entries_[idx - 1];
    }

    EntryIdx try_alloc_entry_idx() {
        if (entries_count_ == NUM_ENTRY_SLOTS) {
#ifndef FUZZ_TEST
            xprintf("%s: hash table is full", __PRETTY_FUNCTION__);
#endif
            return 0;
        }
        return (EntryIdx)++entries_count_;
    }

    size_t hash(const KeyTy &key) {
        return (size_t)key % NUM_HASH_SLOTS;
    }

    Entry entries_[NUM_ENTRY_SLOTS]{};
    size_t entries_count_ = 0;
    Packed<EntryIdx> by_hash_[NUM_HASH_SLOTS]{};
};

#ifdef FUZZ_TEST
/*
clang++ -std=c++20 -o /tmp/sh-fuzz -fsanitize=fuzzer -DFUZZ_TEST -x c++ stupid_hash.hpp -O2 -g3
*/
#include <unordered_map>
#include <span>
#include <vector>
namespace fuzz {
    using KeyTy = UInt48;
    using ValTy = Packed<uint32_t>;

    struct HashKeyTy {
        size_t operator()(const KeyTy &key) const {
            return key;
        }
    };

    using SH = StupidHash<KeyTy, ValTy, 3000, 3000>;
    using UM = std::unordered_map<KeyTy, ValTy, HashKeyTy>;

    template <typename T>
    T shift(std::span<const uint8_t> *span) {
        T t{};
        size_t size = std::min(span->size(), sizeof(T));
        memcpy(&t, span->data(), size);
        *span = span->subspan(size);
        return t;
    }

    static void verify(SH &sh, const UM &um) {
        std::vector<std::pair<KeyTy, ValTy>> sh_kvs, um_kvs;
        for (SH::Entry &entry : sh) {
            sh_kvs.emplace_back(entry.key, entry.value);
        }
        for (const auto &pair : um) {
            um_kvs.emplace_back(pair);
        }
        std::sort(sh_kvs.begin(), sh_kvs.end());
        std::sort(um_kvs.begin(), um_kvs.end());
        assert(sh_kvs == um_kvs);
    }

    extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
        std::span<const uint8_t> span(data, size);
        SH sh;
        UM um;
        for (size_t i = 0; i < sizeof(sh); i++) {
            assert(((char *)&sh)[i] == 0);
        }
        while (!span.empty()) {
            assert(sh.entries_count_ == um.size());
            switch (shift<int>(&span) % 2) {
            case 0: {
                auto key = shift<KeyTy>(&span);
                auto val = shift<ValTy>(&span);
                if (sh.size() == sh.capacity()) {
                    break;
                }
                auto it = um.find(key);
                bool insert = shift<uint8_t>(&span);
                auto [entry, was_found] = sh.lookup(key, insert);
                if (was_found) {
                    assert(it != um.end());
                    assert(entry);
                    assert(entry->key == key);
                    assert(it->second == entry->value);
                    it->second = val;
                    entry->value = val;
                } else {
                    assert(it == um.end());
                    if (insert) {
                        assert(entry);
                        assert(entry->key == key);
                        entry->value = val;
                        um[key] = val;
                    } else {
                        assert(!entry);
                    }
                }
                break;
            }
            case 1:  {
                if (sh.size() == 0) {
                    break;
                }
                KeyTy key = sh.entries_[shift<uint32_t>(&span) % sh.size()].key;
                auto [entry, was_found] = sh.lookup(key, false);
                assert(was_found);
                sh.remove(entry);
                auto [entry2, was_found2] = sh.lookup(key, false);
                assert(!was_found2);
                assert(um.erase(key) == 1);
                break;
            }
            }

        }
        verify(sh, um);
        return 0;
    }

} // namespace fuzz
#endif
