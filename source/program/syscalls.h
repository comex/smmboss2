#pragma once
#include "convert_errno.h"
#include <sys/cdefs.h>
#include <sys/types.h>
#include <stdlib.h>
#include <errno.h>

__BEGIN_DECLS

typedef uint32_t socklen_t;
typedef uint8_t sa_family_t;

#define AF_INET 2

#define SOCK_STREAM 1
#define SOCK_DGRAM 2
#define IPPROTO_TCP 6
#define IPPROTO_UDP 17

#define TCP_NODELAY 1

#define SOL_SOCKET 0xffff
#define SO_REUSEADDR 0x0004
#define SO_KEEPALIVE 0x0008

struct in_addr {
    uint32_t s_addr;
};

struct sockaddr {
    unsigned char   sa_len;
    sa_family_t sa_family;
    char        sa_data[14];
};

struct sockaddr_in {
    uint8_t sin_len;
    sa_family_t sin_family;
    in_port_t   sin_port;
    struct  in_addr sin_addr;
    char    sin_zero[8];
};

typedef unsigned int nfds_t;

struct pollfd {
    int fd;
    short   events;
    short   revents;
};

#define POLLIN      0x0001
#define POLLPRI     0x0002
#define POLLOUT     0x0004
#define POLLRDNORM  0x0040
#define POLLWRNORM  POLLOUT
#define POLLRDBAND  0x0080
#define POLLWRBAND  0x0100
#define POLLERR     0x0008
#define POLLHUP     0x0010
#define POLLNVAL    0x0020


int *___errno_location();

// Macro to define a function that wraps an SDK libc function that returns
// errors using errno.  The wrapper will take the errno value from the SDK's
// copy of errno, convert it, and store it in our copy of errno.
#define ERRNO_WRAPPER(decoration, return_type, standard_name, sdk_name, /*arg_types*/...) \
    extern return_type sdk_name(__VA_ARGS__); \
    decoration return_type standard_name(ERRNO_WRAPPER_APPLY(ERRNO_WRAPPER_ARG_DEF, __VA_ARGS__)) { \
        int *sdk_errno_p = ___errno_location(); \
        *sdk_errno_p = 0; \
        return_type ret = sdk_name(ERRNO_WRAPPER_APPLY(ERRNO_WRAPPER_ARG_NAME, __VA_ARGS__)); \
        errno = _convert_errno(*sdk_errno_p); \
        return ret; \
    }

// Helper macros.
#define ERRNO_WRAPPER_APPLY(func, ...) __VA_OPT__(ERRNO_WRAPPER_APPLY1(func, __VA_ARGS__))
#define ERRNO_WRAPPER_APPLY1(func, arg_type, ...) func(arg_type, a1) __VA_OPT__(, ERRNO_WRAPPER_APPLY2(func, __VA_ARGS__))
#define ERRNO_WRAPPER_APPLY2(func, arg_type, ...) func(arg_type, a2) __VA_OPT__(, ERRNO_WRAPPER_APPLY3(func, __VA_ARGS__))
#define ERRNO_WRAPPER_APPLY3(func, arg_type, ...) func(arg_type, a3) __VA_OPT__(, ERRNO_WRAPPER_APPLY4(func, __VA_ARGS__))
#define ERRNO_WRAPPER_APPLY4(func, arg_type, ...) func(arg_type, a4) __VA_OPT__(, ERRNO_WRAPPER_APPLY5(func, __VA_ARGS__))
#define ERRNO_WRAPPER_APPLY5(func, arg_type, ...) func(arg_type, a5) __VA_OPT__(, ERRNO_WRAPPER_APPLY6(func, __VA_ARGS__))
#define ERRNO_WRAPPER_APPLY6(func, arg_type) func(arg_type, a6)

#define ERRNO_WRAPPER_ARG_DEF(arg_type, arg_name) arg_type arg_name
#define ERRNO_WRAPPER_ARG_NAME(arg_type, arg_name) arg_name


// Wrappers for functions that devkitA64 headers do not define:
// TODO: might want to switch to a custom implementation
ERRNO_WRAPPER(static inline, int, getsockname, nnsocketGetSockName,
              int, struct sockaddr *, socklen_t *);
ERRNO_WRAPPER(static inline, ssize_t, send, nnsocketSend,
              int, const void *, size_t, int);
ERRNO_WRAPPER(static inline, ssize_t, sendto,nnsocketSendTo,
              int, const void *, size_t, int, const struct sockaddr *, socklen_t);
ERRNO_WRAPPER(static inline, ssize_t, recv, nnsocketRecv,
              int, void *, size_t, int);
ERRNO_WRAPPER(static inline, ssize_t, recvfrom, nnsocketRecvFrom,
              int, void *, size_t, int, struct sockaddr *, socklen_t *);
ERRNO_WRAPPER(static inline, int, bind, nnsocketBind,
              int, const struct sockaddr *, socklen_t);
ERRNO_WRAPPER(static inline, int, socket, nnsocketSocket,
              int, int, int);
ERRNO_WRAPPER(static inline, int, listen, nnsocketListen,
              int, int);
ERRNO_WRAPPER(static inline, int, getpeername, nnsocketGetPeerName,
              int, struct sockaddr *, socklen_t *);
ERRNO_WRAPPER(static inline, int, connect, nnsocketConnect,
              int, const struct sockaddr *, socklen_t);
ERRNO_WRAPPER(static inline, int, accept, nnsocketAccept,
              int, struct sockaddr *, socklen_t *);
ERRNO_WRAPPER(static inline, int, setsockopt, nnsocketSetSockOpt,
              int, int, int, const void *, socklen_t);
ERRNO_WRAPPER(static inline, int, poll, nnsocketPoll,
              struct pollfd *, nfds_t, int);

int nnsocketInitialize(void *tmem, uint64_t tmem_end_offset, uint64_t tmem_start_offset, int max_sess);

// this is from the SDK, but based on the thread implementation in
// syscalls.cpp, pthread_t is equivalent between newlib and the SDK pthreads
int pthread_setname_np(pthread_t thread, const char *name);

__END_DECLS
