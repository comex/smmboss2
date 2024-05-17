// Copyright (c) 2022-2023 Cesanta Software Limited
// All rights reserved

#include "hal.h"
#include "mongoose.h"
#include "net.h"

#define LED1 PIN('N', 1)  // On-board LED pin
#define LED2 PIN('N', 0)  // On-board LED pin
#define LED3 PIN('F', 4)  // On-board LED pin
#define LED4 PIN('F', 0)  // On-board LED pin

#define LED LED1              // Use this LED for blinking
#define BLINK_PERIOD_MS 1000  // LED blinking period in millis

static void timer_fn(void *arg) {
  struct mg_tcpip_if *ifp = arg;                         // And show
  const char *names[] = {"down", "up", "req", "ready"};  // network stats
  MG_INFO(("Ethernet: %s, IP: %M, rx:%u, tx:%u, dr:%u, er:%u",
           names[ifp->state], mg_print_ip4, &ifp->ip, ifp->nrecv, ifp->nsent,
           ifp->ndrop, ifp->nerr));
}

static void ethernet_init(void) {
  // Initialise Ethernet. Enable MAC GPIO pins, see
  // https://www.ti.com/lit/pdf/spms433
  // Assign LED3 and LED4 to the EPHY, "activity" and "link", respectively.
  // (20.4.2.4)
  gpio_init(LED3, GPIO_MODE_AF, GPIO_OTYPE_PUSH_PULL, GPIO_SPEED_HIGH,
            GPIO_PULL_NONE, 5);  // EN0LED1
  gpio_init(LED4, GPIO_MODE_AF, GPIO_OTYPE_PUSH_PULL, GPIO_SPEED_HIGH,
            GPIO_PULL_NONE, 5);  // EN0LED0
  NVIC_EnableIRQ(EMAC0_IRQn);    // Setup Ethernet IRQ handler
  // Initialize Ethernet clocks, see datasheet section 5
  // Turn Flash Prefetch off (silicon errata ETH#02)
  uint32_t val = FLASH_CTRL->CONF;
  val &= ~BIT(17);
  val |= BIT(16);
  FLASH_CTRL->CONF = val;
  SYSCTL->RCGCEMAC |= BIT(0);  // Enable EMAC clock
  SYSCTL->SREMAC |= BIT(0);    // Reset EMAC
  SYSCTL->SREMAC &= ~BIT(0);
  SYSCTL->RCGCEPHY |= BIT(0);  // Enable EPHY clock
  SYSCTL->SREPHY |= BIT(0);    // Reset EPHY
  SYSCTL->SREPHY &= ~BIT(0);
  while (!(SYSCTL->PREMAC & BIT(0)) || !(SYSCTL->PREPHY & BIT(0)))
    spin(1);  // Wait for reset to complete
}

static void server(void *args) {
  struct mg_mgr mgr;        // Initialise Mongoose event manager
  mg_mgr_init(&mgr);        // and attach it to the interface
  mg_log_set(MG_LL_DEBUG);  // Set log level

  // Initialise Mongoose network stack
  ethernet_init();
  struct mg_tcpip_driver_tm4c_data driver_data = {.mdc_cr = 1};
  struct mg_tcpip_if mif = {
      .mac = READ_PREFLASHED_MAC(),
      // Uncomment below for static configuration:
      // .ip = mg_htonl(MG_U32(192, 168, 0, 223)),
      // .mask = mg_htonl(MG_U32(255, 255, 255, 0)),
      // .gw = mg_htonl(MG_U32(192, 168, 0, 1)),
      .driver = &mg_tcpip_driver_tm4c,
      .driver_data = &driver_data,
  };
  mg_tcpip_init(&mgr, &mif);
  uint32_t val = FLASH_CTRL->CONF;  // Turn Flash Prefetch on again
  val &= ~BIT(16);
  val |= BIT(17);
  FLASH_CTRL->CONF = val;
  mg_timer_add(&mgr, BLINK_PERIOD_MS, MG_TIMER_REPEAT, timer_fn, &mif);

  MG_INFO(("MAC: %M. Waiting for IP...", mg_print_mac, mif.mac));
  while (mif.state != MG_TCPIP_STATE_READY) {
    mg_mgr_poll(&mgr, 0);
  }

  MG_INFO(("Initialising application..."));
  web_init(&mgr);

  MG_INFO(("Starting event loop"));
  for (;;) mg_mgr_poll(&mgr, 1);  // Infinite event loop
  (void) args;
}

static void blinker(void *args) {
  gpio_init(LED, GPIO_MODE_OUTPUT, GPIO_OTYPE_PUSH_PULL, GPIO_SPEED_HIGH,
            GPIO_PULL_NONE, 0);
  for (;;) {
    gpio_toggle(LED);
    vTaskDelay(pdMS_TO_TICKS(BLINK_PERIOD_MS));
  }
  (void) args;
}

int main(void) {
  uart_init(UART_DEBUG, 115200);  // Initialise UART

  // Start tasks. NOTE: stack sizes are in 32-bit words
  xTaskCreate(blinker, "blinker", 128, ":)", configMAX_PRIORITIES - 1, NULL);
  xTaskCreate(server, "server", 2048, 0, configMAX_PRIORITIES - 1, NULL);

  vTaskStartScheduler();  // This blocks
  return 0;
}
