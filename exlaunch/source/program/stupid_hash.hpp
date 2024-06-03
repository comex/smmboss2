#include <stdint.h>
#include <stddef.h>
#include <utility>

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

#pragma once
template <typename KeyTy, typename ValTy, size_t NUM_HASH_SLOTS, size_t NUM_ENTRY_SLOTS>
struct StupidHash {
    using EntryIdx = uint16_t;
    static_assert(NUM_ENTRY_SLOTS < (1 << (8 * sizeof(EntryIdx))));
    static constexpr EntryIdx SENTINEL_ENTRY_IDX = (EntryIdx)-1;

    struct Entry {
        Packed<EntryIdx> prev, next;
        KeyTy key;
        ValTy value;
    };

    EntryIdx *prevs_next(Entry *entry) {
        if (entry->prev == SENTINEL_ENTRY_IDX) {
            return &by_hash_[hash(entry->key)];
        } else {
            return &entries_[entry->prev].next;
        }
    }

    EntryIdx *nexts_prev(Entry *entry) {
        if (entry->next == SENTINEL_ENTRY_IDX) {
            return nullptr;
        } else {
            return &entries_[entry->prev].prev;
        }
    }

    void move_entry(EntryIdx old_idx, EntryIdx new_idx) {
        Entry *old_entry = &entries_[old_idx];
        Entry *new_entry = &entries_[new_idx];
        *new_entry = *old_entry;

        *prevs_next(old_entry) = new_idx;
        if (EntryIdx *p = nexts_prev(old_entry)) {
            *p = new_idx;
        }
    }

    void unlink_entry(EntryIdx entry_idx) {
        Entry *entry = &entries_[entry_idx];
        EntryIdx prev = entry->prev, next = entry->next;

        *prevs_next(entry) = next;
        if (EntryIdx *p = nexts_prev(entry)) {
            *p = prev;
        }
    }

    void remove_entry(Entry *entry) {
        EntryIdx entry_idx = entry_to_idx(entry);
        unlink_entry(entry_idx);
        assert(entries_count_ > 0);
        EntryIdx victim_idx = (EntryIdx)--entries_count_;
        if (victim_idx != entry_idx) {
            move_entry(victim_idx, entry_idx);
        }
    }

    EntryIdx entry_to_idx(Entry *entry) {
        size_t offset = (uintptr_t)entry - (uintptr_t)entries_;
        assert(offset < sizeof(entries_) && offset % sizeof(Entry) == 0);
        return offset / sizeof(Entry);
    }

    std::pair<Entry *, bool> find_entry(KeyTy key, bool insert) {
        EntryIdx *next_p = &by_hash_[hash(key)], idx = *next_p;
        EntryIdx expected_prev = SENTINEL_ENTRY_IDX;
        Entry *entry;
        while (idx != SENTINEL_ENTRY_IDX) {
            entry = &entries_[idx];
            assert(entry->prev == expected_prev);
            if (entry->key == key) {
                return std::make_pair(entry, true);
            }
            expected_prev = idx;
            next_p = &entry->next;
            idx = *next_p;
        }
        if (insert) {
            EntryIdx new_idx = alloc_entry_idx();
            *next_p = new_idx;
            entry = &entries_[new_idx];
            entry->prev = expected_prev;
            entry->next = SENTINEL_ENTRY_IDX;
            entry->key = key;
        } else {
            entry = nullptr;
        }
        return std::make_pair(entry, false);
    }

    EntryIdx alloc_entry_idx() {
        if (entries_count_ == NUM_ENTRY_SLOTS) {
            panic("%s: hash table is full", __PRETTY_FUNCTION__);
        }
        return (EntryIdx)entries_count_++;
    }

    Entry entries_[NUM_ENTRY_SLOTS];
    size_t entries_count_;
    EntryIdx by_hash_[NUM_HASH_SLOTS];
};
