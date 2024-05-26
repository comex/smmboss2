#pragma once
#define MG_ARCH MG_ARCH_CUSTOM

#include <stdint.h>
#include <stdlib.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/fcntl.h>

typedef uint32_t socklen_t; // check
typedef uint8_t sa_family_t; // check

#define AF_INET 2

#define SOCK_STREAM 1
#define SOCK_DGRAM 2
#define IPPROTO_TCP 6
#define IPPROTO_UDP 17

#define TCP_NODELAY 1

#define SOL_SOCKET 0xffff
#define SO_KEEPALIVE 0x0008

// check
struct in_addr {
    uint32_t s_addr;
};

// check
struct sockaddr {
    unsigned char   sa_len;
    sa_family_t sa_family;
    char        sa_data[14];
};

// check
struct sockaddr_in {
    uint8_t sin_len;
    sa_family_t sin_family;
    in_port_t   sin_port;
    struct  in_addr sin_addr;
    char    sin_zero[8];
};

int getsockname(int sockfd, struct sockaddr *addr, socklen_t *addrlen) asm("nnsocketGetSockName");
ssize_t send(int sockfd, const void* buf, size_t len, int flags) asm("nnsocketSend");
ssize_t sendto(int sockfd, const void *buf, size_t len, int flags, const struct sockaddr *dest_addr, socklen_t addrlen) asm("nnsocketSendTo");
ssize_t recv(int sockfd, void *buf, size_t len, int flags) asm("nnsocketRecv");
ssize_t recvfrom(int sockfd, void *buf, size_t len, int flags, struct sockaddr *src_addr, socklen_t *addrlen) asm("nnsocketRecvFrom");
int fcntl(int fd, int cmd, ...) asm("nnsocketFcntl");
int bind(int sockfd, const struct sockaddr *addr, socklen_t addrlen) asm("nnsocketBind");
int socket(int domain, int type, int protocol) asm("nnsocketSocket");
int listen(int sockfd, int backlog) asm("nnsocketListen");
int close(int sockfd) asm("nnsocketClose");
int getpeername(int sockfd, struct sockaddr *addr, socklen_t *addrlen) asm("nnsocketGetPeerName");
int connect(int sockfd, const struct sockaddr *addr, socklen_t addrlen) asm("nnsocketConnect");
int accept(int sockfd, struct sockaddr *addr, socklen_t *addrlen) asm("nnsocketAccept");
int setsockopt(int sockfd, int level, int optname, const void *optval, socklen_t optlen) asm("nnsocketSetSockOpt");

int select(int nfds, fd_set *readfds, fd_set *writefds, fd_set *errorfds, struct timeval *timeout) asm("nnsocketSelect");
_Static_assert(sizeof(struct fd_set) == 0x80, "did -DFD_SETSIZE=1024 not work?");

int *___errno_location();
#define MG_SOCKET_ERRNO *___errno_location()

