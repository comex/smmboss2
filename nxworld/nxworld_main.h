#include <stdint.h>
#include <sys/cdefs.h>
__BEGIN_DECLS
void nxworld_main(uint32_t main_thread_handle);

// calls back into exl world
void log_str(const char *str);
__attribute__((format(printf, 1, 2)))
void xprintf(const char *fmt, ...);
__END_DECLS
