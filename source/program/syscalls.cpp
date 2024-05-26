#include "stuff.hpp"
#include <sys/lock.h>
#include <sys/iosupport.h>

namespace nn {
    namespace os {
        namespace detail {
            struct InternalCriticalSection;
            struct InternalCriticalSectionImplByHorizon : public InternalCriticalSection {
                void Enter();
                bool TryEnter();
                void Leave();

                _LOCK_T lock;
                static inline InternalCriticalSectionImplByHorizon *_from_lock(_LOCK_T *lock) {
                    return (InternalCriticalSectionImplByHorizon *)lock;
                }
            };
            struct TimeoutHelper {
            };
            struct InternalConditionVariableImplByHorizon {
                void Signal();
                void Broadcast();
                void Wait(InternalCriticalSection *);
                bool TimedWait(InternalCriticalSection *, const TimeoutHelper &);
                
                _COND_T cond;
                static inline InternalConditionVariableImplByHorizon *_from_cond(_COND_T *cond) {
                    return (InternalConditionVariableImplByHorizon *)cond;
                }
            };
        }
    }
}
                

extern "C" {

struct sdk_pthread_t;
struct sdk_pthread_attr_t {
    long opaque[0x38/8];
};

struct sdk_pthread_mutex_t {
    long opaque[0x20/8];
};
struct sdk_pthread_mutexattr_t;

void sdk_exit(int rc);
sdk_pthread_t *sdk_pthread_self();
void sdk_pthread_exit(void *);
int sdk_pthread_join(sdk_pthread_t *, void **);
int sdk_pthread_detach(sdk_pthread_t *);

int sdk_pthread_attr_init(sdk_pthread_t *);
int sdk_pthread_attr_setstack(sdk_pthread_t *, void *, size_t);

void __syscall_exit(int rc) {
    sdk_exit(rc);
}
struct _reent* __syscall_getreent(void) {
    panic("TODO");
}
void __syscall_lock_acquire(_LOCK_T *lock) {
    nn::os::detail::InternalCriticalSectionImplByHorizon::_from_lock(lock).Enter();
}
int __syscall_lock_try_acquire(_LOCK_T *lock) {
    return !nn::os::detail::InternalCriticalSectionImplByHorizon::_from_lock(lock).TryEnter();
}
void __syscall_lock_release(_LOCK_T *lock) {
    nn::os::detail::InternalCriticalSectionImplByHorizon::_from_lock(lock).Leave();
}
void __syscall_lock_acquire_recursive(_LOCK_T *lock) {
    panic("TODO");
}
int __syscall_lock_try_acquire_recursive(_LOCK_T *lock) {
    panic("TODO");
}
void __syscall_lock_release_recursive(_LOCK_T *lock) {
    panic("TODO");
}
int __syscall_cond_signal(_COND_T *cond) {
    nn::os::detail::InternalConditionVariableImplByHorizon::_from_cond(cond).Signal();
    return 0;
}
int __syscall_cond_broadcast(_COND_T *cond) {
    nn::os::detail::InternalConditionVariableImplByHorizon::_from_cond(cond).Broadcast();
    return 0;
}
int __syscall_cond_wait(_COND_T *cond, _LOCK_T *lock, uint64_t timeout_ns) {
    nn::os::detail::InternalConditionVariableImplByHorizon::_from_cond(cond).Wait();
    return 0;
}
int __syscall_cond_wait_recursive(_COND_T *cond, _LOCK_T *lock, uint64_t timeout_ns) {
    panic("TODO");
}
sdk_pthread_t *__syscall_thread_self(void) {
    return sdk_pthread_self();
}
void __syscall_thread_exit(void *value) {
    sdk_pthread_exit(value);
}
int __syscall_thread_create(sdk_pthread_t **thread, void* (*func)(void*), void *arg, void *stack_addr, size_t stack_size) {
    assert(!!stack_addr == !!stack_size);
    sdk_pthread_attr_t attr;
    assert(!sdk_pthread_attr_init(&attr));
    if (stack_addr) {
        assert(!sdk_pthread_attr_setstack(&attr, stack_addr, stack_size));
    }
    // no need for attr_destroy
    return sdk_pthread_create(thread, &attr, func, arg);
}
void* __syscall_thread_join(sdk_pthread_t *thread) {
    void *val;
    if (!sdk_pthread_join(thread, &val)) {
        return val;
    } else {
        return nullptr;
    }
}
int __syscall_thread_detach(sdk_pthread_t *thread) {
    return sdk_pthread_detach(thread);
}
int __syscall_tls_create(uint32_t *key, void (*destructor)(void*)) {
}
int __syscall_tls_set(uint32_t key, const void *value) {
}
void* __syscall_tls_get(uint32_t key) {
}
int __syscall_tls_delete(uint32_t key) {
}
int __syscall_clock_getres(clockid_t clock_id, struct timespec *tp) {
    panic("clock_getres");
}
int __syscall_clock_gettime(clockid_t clock_id, struct timespec *tp) {
    panic("clock_gettime");
}
int __syscall_gettod_r(struct _reent *ptr, struct timeval *tp, struct timezone *tz) {
    panic("gettod_r");
}
int __syscall_nanosleep(const struct timespec *req, struct timespec *rem) {
    // copied from libinx
    svcSleepThread(timespec2nsec(req));
    if (rem) {
        rem->tv_nsec = 0;
        rem->tv_sec = 0;
    }
    return 0;
}

}  // extern "C"
