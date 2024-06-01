#pragma once
#include <stdlib.h>

void log_str(const char *str);
__attribute__((format(printf, 1, 2)))
void xprintf(const char *fmt, ...);

#define panic(...) do { \
    xprintf(__VA_ARGS__); \
    abort(); \
} while (0)

#undef assert
#define assert(expr) do { \
    if (!(expr)) { \
        panic("assertion failed: %s", #expr); \
    } \
} while (0)

// --

#define PROP(name, offset, ...) \
    using typeof_##name = __VA_ARGS__; \
    static constexpr size_t offsetof_##name() { \
        return offset; \
    } \
    typeof_##name &name() { \
        return *(typeof_##name *)((char *)this + offsetof_##name()); \
    } \
    const typeof_##name &name() const { \
        return *(typeof_##name *)((char *)this + offsetof_##name()); \
    }

#define PSEUDO_TYPE_SIZE(size) \
    static constexpr size_t size_of() { \
        return size; \
    } \
    static constexpr bool _is_pseudo_type = true

#define PSEUDO_TYPE_UNSIZED \
    static constexpr bool _is_pseudo_type = true

// A type that uses PROP macros for its fields.
template <typename T>
concept pseudo_type = requires { { T::_is_pseudo_type }; };

template <pseudo_type T>
constexpr inline size_t _pt_size_of() { return T::size_of(); }

template <typename T>
constexpr inline size_t _pt_size_of() { return sizeof(T); }

template <typename T>
constexpr inline size_t pt_size_of = _pt_size_of<T>();

// Pointer to a pseudo-type.  Normal pointer arithmetic won't work because the
// type doesn't have any real fields, so sizeof will give the wrong answer.
// Can also be used for normal types.
template <typename T>
struct pt_pointer {
    T *raw;

    pt_pointer(T *raw) : raw(raw) {}
    T &operator*() const { return *raw; }
    T *operator->() const { return raw; }
    explicit operator bool() const { return !!raw; }
    T &operator[](size_t i) const { return *(*this + i); }

    pt_pointer operator+(size_t n) const {
        return (T *)((char *)raw + n * pt_size_of<T>);
    }

    pt_pointer &operator++() {
        return *this = *this + 1;
    }

    bool operator==(const pt_pointer &other) const { return raw == other.raw; }
};

template <pseudo_type T, size_t count>
struct pt_array {
    pt_pointer<T> operator*() const { return *(T *)this; }
    T *operator->() const { return (T *)this; }
    T &operator[](size_t i) const { return *(*this + i); }

    pt_pointer<T> operator+(size_t n) const {
        return (T *)((char *)this + n * pt_size_of<T>);
    }

    static constexpr size_t size_of() {
        return pt_size_of<T> * count;
    }
    static constexpr bool _pseudo_type = true;
};
