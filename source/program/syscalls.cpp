#include "stuff.hpp"
#include <sys/lock.h>
#include <sys/iosupport.h>
#include <atomic>
#include "nn/os/os_condition_variable_common.hpp"
#include "nn/os/os_thread_type.hpp"

using namespace nn;
using namespace nn::os;
using namespace nn::os::detail;

extern "C" {

#define SDK_ALIAS(sym) \
    asm(".symver sdk_" #sym "," #sym "@")

int *___errno_location();

SDK_ALIAS(clock_getres);
int sdk_clock_getres(int clock_id, struct timespec *tp);

SDK_ALIAS(clock_gettime);
int sdk_clock_gettime(int clock_id, struct timespec *tp);

using sdk_pthread_t = struct __pthread_t *;
struct sdk_pthread_attr_t {
    long opaque[0x38/8];
};
typedef uint32_t sdk_pthread_key_t;

SDK_ALIAS(pthread_exit);
void sdk_exit(int rc);

SDK_ALIAS(pthread_self);
sdk_pthread_t sdk_pthread_self();

SDK_ALIAS(pthread_create);
int sdk_pthread_create(sdk_pthread_t *, const sdk_pthread_attr_t *, void *(*)(void *), void *);

SDK_ALIAS(pthread_exit);
void sdk_pthread_exit(void *);

SDK_ALIAS(pthread_join);
int sdk_pthread_join(sdk_pthread_t, void **);

SDK_ALIAS(pthread_detach);
int sdk_pthread_detach(sdk_pthread_t);

SDK_ALIAS(pthread_attr_init);
int sdk_pthread_attr_init(sdk_pthread_attr_t *);

SDK_ALIAS(pthread_attr_setstack);
int sdk_pthread_attr_setstack(sdk_pthread_attr_t *, void *, size_t);

SDK_ALIAS(pthread_key_create);
int sdk_pthread_key_create(sdk_pthread_key_t *, void (*)(void *));

SDK_ALIAS(pthread_key_delete);
int sdk_pthread_key_delete(sdk_pthread_key_t);

SDK_ALIAS(pthread_setspecific);
int sdk_pthread_setspecific(sdk_pthread_key_t, const void *);

SDK_ALIAS(pthread_getspecific);
void *sdk_pthread_getspecific(sdk_pthread_key_t);

#pragma GCC diagnostic error "-Wmissing-declarations"

void __syscall_exit(int rc) {
    sdk_exit(rc);
}

static _LOCK_T _reent_lock;
static std::atomic<sdk_pthread_key_t> _reent_key;
struct _reent* __syscall_getreent(void) {
    sdk_pthread_key_t key = _reent_key.load(std::memory_order_acquire);
    if (!key) {
        __syscall_lock_acquire(&_reent_lock);
        key = _reent_key.load(std::memory_order_relaxed);
        if (!key) {
            if (__syscall_tls_create(&key, free)) {
                abort();
            }
            _reent_key.store(key, std::memory_order_release);
        }
        __syscall_lock_release(&_reent_lock);
    }
    auto reent = (struct _reent *)__syscall_tls_get(key);
    if (!reent) {
        reent = (struct _reent *)calloc(1, sizeof(struct _reent));
        if (!reent || __syscall_tls_set(key, reent)) {
            abort();
        }
    }
    return reent;
}
void __syscall_lock_acquire(_LOCK_T *lock) {
    ((InternalCriticalSectionImplByHorizon *)lock)->Enter();
}
int __syscall_lock_try_acquire(_LOCK_T *lock) {
    return ((InternalCriticalSectionImplByHorizon *)lock)->TryEnter();
}
void __syscall_lock_release(_LOCK_T *lock) {
    ((InternalCriticalSectionImplByHorizon *)lock)->Leave();
}
void __syscall_lock_acquire_recursive(_LOCK_RECURSIVE_T *lock) {
    // loosely based on libnx
    uint32_t my_tag = nn::os::GetCurrentThread()->handle;
    if (lock->thread_tag != my_tag) {
        __syscall_lock_acquire(&lock->lock);
        lock->thread_tag = my_tag;
    }

    lock->counter++;
}
int __syscall_lock_try_acquire_recursive(_LOCK_RECURSIVE_T *lock) {
    // loosely based on libnx
    uint32_t my_tag = nn::os::GetCurrentThread()->handle;
    if (lock->thread_tag != my_tag) {
        if (__syscall_lock_try_acquire(&lock->lock)) {
            return 1;
        }
        lock->thread_tag = my_tag;
    }

    lock->counter++;
    return 0;
}
void __syscall_lock_release_recursive(_LOCK_RECURSIVE_T *lock) {
    // loosely based on libnx
    if (--lock->counter == 0) {
        lock->thread_tag = 0;
        __syscall_lock_release(&lock->lock);
    }
}
int __syscall_cond_signal(_COND_T *cond) {
    ((InternalConditionVariableImplByHorizon *)cond)->Signal();
    return 0;
}
int __syscall_cond_broadcast(_COND_T *cond) {
    ((InternalConditionVariableImplByHorizon *)cond)->Broadcast();
    return 0;
}
int __syscall_cond_wait(_COND_T *cond, _LOCK_T *lock, uint64_t timeout_ns) {
    auto cond_impl = (InternalConditionVariableImplByHorizon *)cond;
    auto lock_int = (InternalCriticalSection *)lock;
    if (timeout_ns == UINT64_MAX) {
        cond_impl->Wait(lock_int);
        return 0;
    } else {
        switch (cond_impl->TimedWait(lock_int, TimeoutHelper(TimeSpan::FromNanoSeconds(timeout_ns)))) {
        case ConditionVariableStatus::TimedOut:
            return ETIMEDOUT;
        case ConditionVariableStatus::Success:
            return 0;
        default:
            abort();
        }
    }
}
int __syscall_cond_wait_recursive(_COND_T *cond, _LOCK_RECURSIVE_T *lock, uint64_t timeout_ns) {
    // based on libnx
    if (lock->counter != 1) {
        return EBADF;
    }

    uint32_t thread_tag_backup = lock->thread_tag;
    lock->thread_tag = 0;
    lock->counter = 0;

    int errcode = __syscall_cond_wait(cond, &lock->lock, timeout_ns);

    lock->thread_tag = thread_tag_backup;
    lock->counter = 1;

    return errcode;
}
sdk_pthread_t __syscall_thread_self(void) {
    return sdk_pthread_self();
}
void __syscall_thread_exit(void *value) {
    sdk_pthread_exit(value);
}
int __syscall_thread_create(sdk_pthread_t *thread, void* (*func)(void*), void *arg, void *stack_addr, size_t stack_size) {
    assert(!!stack_addr == !!stack_size);
    sdk_pthread_attr_t attr;
    assert(!sdk_pthread_attr_init(&attr));
    if (stack_addr) {
        assert(!sdk_pthread_attr_setstack(&attr, stack_addr, stack_size));
    }
    // no need for attr_destroy
    return sdk_pthread_create(thread, &attr, func, arg);
}
void* __syscall_thread_join(sdk_pthread_t thread) {
    void *val;
    if (!sdk_pthread_join(thread, &val)) {
        return val;
    } else {
        return nullptr;
    }
}
int __syscall_thread_detach(sdk_pthread_t thread) {
    return sdk_pthread_detach(thread);
}
int __syscall_tls_create(uint32_t *key, void (*destructor)(void*)) {
    return sdk_pthread_key_create(key, destructor);
}
int __syscall_tls_set(uint32_t key, const void *value) {
    return sdk_pthread_setspecific(key, value);
}
void* __syscall_tls_get(uint32_t key) {
    return sdk_pthread_getspecific(key);
}
int __syscall_tls_delete(uint32_t key) {
    return sdk_pthread_key_delete(key);
}

static constexpr u64 nsec_clockres =  1000000000ULL / 19200000ULL;

int __syscall_clock_getres(clockid_t clock_id, struct timespec *tp) {
    int ret = sdk_clock_getres(0, tp);
    errno = *___errno_location();
    return ret;
}
int __syscall_clock_gettime(clockid_t clock_id, struct timespec *tp) {
    int ret = sdk_clock_gettime(0, tp);
    errno = *___errno_location();
    return ret;
}
int __syscall_gettod_r(struct _reent *ptr, struct timeval *tv, struct timezone *tz) {
    struct timespec tp;
    if (__syscall_clock_gettime(CLOCK_REALTIME, &tp)) {
        return -1;
    }
    tv->tv_sec = tp.tv_sec;
    tv->tv_usec = tp.tv_nsec / 1000;
    if (tz != NULL) {
        tz->tz_minuteswest = 0;
        tz->tz_dsttime = 0;
    }
    return 0;
}
int __syscall_nanosleep(const struct timespec *req, struct timespec *rem) {
    TimeoutHelper::Sleep(TimeSpan::FromNanoSeconds(timespec2nsec(req)));
    if (rem) {
        rem->tv_nsec = 0;
        rem->tv_sec = 0;
    }
    return 0;
}

}  // extern "C"

