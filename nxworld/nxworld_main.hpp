#include <stdint.h>
#include <sys/cdefs.h>
__BEGIN_DECLS
void nxworld_main(uint32_t main_thread_handle);

// calls back into exl world
uint64_t cur_thread_id();
__END_DECLS
