#pragma once
#define MG_ARCH MG_ARCH_CUSTOM
#define MG_ENABLE_POLL 1
#define MG_ENABLE_LOG 0

#include "syscalls.h"
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
#include <sys/cdefs.h>
#include <unistd.h>
