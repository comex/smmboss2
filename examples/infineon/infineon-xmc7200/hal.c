// Copyright (c) 2024 Cesanta Software Limited
// All rights reserved

#include "hal.h"

static volatile uint64_t s_ticks;  // Milliseconds since boot
void SysTick_Handler(void) {       // SyStick IRQ handler, triggered every 1ms
  s_ticks++;
}

void mg_random(void *buf, size_t len) {  // Use on-board RNG
  for (size_t n = 0; n < len; n += sizeof(uint32_t)) {
    uint32_t r = rng_read();
    memcpy((char *) buf + n, &r, n + sizeof(r) > len ? len - n : sizeof(r));
  }
}

uint64_t mg_millis(void) {  // Let Mongoose use our uptime function
  return s_ticks;           // Return number of milliseconds since boot
}

void hal_init(void) {
  clock_init();                            // Set system clock to SYS_FREQUENCY
  SystemCoreClock = SYS_FREQUENCY;         // Update SystemCoreClock global var
  SysTick_Config(SystemCoreClock / 1000);  // Sys tick every 1ms
  rng_init();                              // Initialise random number generator

  uart_init(UART_DEBUG, 115200);  // Initialise UART
  gpio_output(LED1);              // Initialise LED1
  gpio_output(LED2);              // Initialise LED2
  gpio_output(LED3);              // Initialise LED3
  ethernet_init();                // Initialise Ethernet pins
}

#if defined(__ARMCC_VERSION)
// Keil specific - implement IO printf redirection
int fputc(int c, FILE *stream) {
  if (stream == stdout || stream == stderr) uart_write_byte(UART_DEBUG, c);
  return c;
}
#elif defined(__GNUC__)
// ARM GCC specific. ARM GCC is shipped with Newlib C library.
// Implement newlib syscalls:
//      _sbrk() for malloc
//      _write() for printf redirection
//      the rest are just stubs
#include <sys/stat.h>  // For _fstat()

uint32_t SystemCoreClock;
void SystemInit(void) {  // Called automatically by startup code
}

int _fstat(int fd, struct stat *st) {
  (void) fd, (void) st;
  return -1;
}

extern unsigned char _end[];  // End of data section, start of heap. See link.ld
static unsigned char *s_current_heap_end = _end;

size_t hal_ram_used(void) {
  return (size_t) (s_current_heap_end - _end);
}

size_t hal_ram_free(void) {
  unsigned char endofstack;
  return (size_t) (&endofstack - s_current_heap_end);
}

void *_sbrk(int incr) {
  unsigned char *prev_heap;
  unsigned char *heap_end = (unsigned char *) ((size_t) &heap_end - 256);
  prev_heap = s_current_heap_end;
  // Check how much space we  got from the heap end to the stack end
  if (s_current_heap_end + incr > heap_end) return (void *) -1;
  s_current_heap_end += incr;
  return prev_heap;
}

int _open(const char *path) {
  (void) path;
  return -1;
}

int _close(int fd) {
  (void) fd;
  return -1;
}

int _isatty(int fd) {
  (void) fd;
  return 1;
}

int _lseek(int fd, int ptr, int dir) {
  (void) fd, (void) ptr, (void) dir;
  return 0;
}

void _exit(int status) {
  (void) status;
  for (;;) asm volatile("BKPT #0");
}

void _kill(int pid, int sig) {
  (void) pid, (void) sig;
}

int _getpid(void) {
  return -1;
}

int _write(int fd, char *ptr, int len) {
  (void) fd, (void) ptr, (void) len;
  if (fd == 1) uart_write_buf(UART_DEBUG, ptr, (size_t) len);
  return -1;
}

int _read(int fd, char *ptr, int len) {
  (void) fd, (void) ptr, (void) len;
  return -1;
}

int _link(const char *a, const char *b) {
  (void) a, (void) b;
  return -1;
}

int _unlink(const char *a) {
  (void) a;
  return -1;
}

int _stat(const char *path, struct stat *st) {
  (void) path, (void) st;
  return -1;
}

int mkdir(const char *path, mode_t mode) {
  (void) path, (void) mode;
  return -1;
}

void _init(void) {
}
#endif                 // __GNUC__
