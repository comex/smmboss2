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

