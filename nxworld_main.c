#include <switch/types.h>

void __nx_exit() {
    while (1) {}
}

extern void __libnx_init(void* ctx, Handle main_thread, void* saved_lr);
void nxworld_main() {
    __libnx_init(0, 0, 0);

}
