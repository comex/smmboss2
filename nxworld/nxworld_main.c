#include <switch.h>
#include "nxworld_main.h"

void virtmemSetup(void);
void newlibSetup(void);
void __libnx_init_thread(void);

int __nx_applet_type = -2;

void __nx_exit() {
    // shouldn't be called
    log_str("__nx_exit");
    while (1) {}
}

static const SocketInitConfig socket_init_config = {
    .tcp_tx_buf_size        = 0x10000,
    .tcp_rx_buf_size        = 0x10000,
    .tcp_tx_buf_max_size    = 0x10000,
    .tcp_rx_buf_max_size    = 0x10000,

    .udp_tx_buf_size = 256,
    .udp_rx_buf_size = 256,

    .sb_efficiency = 1,

    .num_bsd_sessions = 3,
    .bsd_service_type = BsdServiceType_User,
};

static void early_init(Handle nxworld_thread) {
    log_str("before init");
    envSetup(NULL, nxworld_thread, NULL); 
    newlibSetup();
    virtmemSetup();
    __libnx_init_thread();
    log_str("after init");
    Result rc;
    rc = smInitialize();
    if (R_FAILED(rc)) {
        log_str("smInitialize failed");
        diagAbortWithResult(rc);
    }
    rc = socketInitialize(&socket_init_config);
    if (R_FAILED(rc)) {
        log_str("socketInitialize failed");
        diagAbortWithResult(rc);
    }
    log_str("early_init done");
}


#include "mongoose.h"

#define ALREADY_SENT_RESPONSE 255

struct ws_req {
    uint8_t what;
    union {
        struct {
            uint64_t addr;
            uint64_t len;
        } __attribute__((packed)) rw;
    };

} __attribute__((packed));


static uint8_t handle_ws_packet(const void *buf, size_t len, struct mg_connection *c) {
    if (len < 1) {
        return 0;
    }

    return 0;
}

static void mongoose_callback(struct mg_connection *c, int ev, void *ev_data) {
    if (ev == MG_EV_HTTP_MSG) {  // New HTTP request received
        struct mg_http_message *hm = (struct mg_http_message *) ev_data;
        if (mg_match(hm->uri, mg_str("/ws"), NULL)) {
            mg_ws_upgrade(c, hm, NULL);
        } else if (mg_match(hm->uri, mg_str("/test"), NULL)) {
            //*(volatile int *)0xdeac = 0xf00;
            ((void (*)())0xdead)();
            mg_http_reply(c, 200, "", "ok");
        } else {
            mg_http_reply(c, 404, "", "not found");
        }
    } else if (ev == MG_EV_WS_MSG) {
        struct mg_ws_message *wm = (struct mg_ws_message *)ev_data;
        uint8_t rc = handle_ws_packet(wm->data.buf, wm->data.len, c);
        if (rc != ALREADY_SENT_RESPONSE) {
            mg_ws_send(c, &rc, sizeof(rc), WEBSOCKET_OP_BINARY);
        }
    }
}

static void serve() {
    struct mg_mgr mgr;
    mg_mgr_init(&mgr);
    if (!mg_http_listen(&mgr, "http://0.0.0.0:8000", mongoose_callback, NULL)) {
        log_str("mg_http_listen failed");
        diagAbortWithResult(MAKERESULT(444, 444));
    }
    while (1) {
        mg_mgr_poll(&mgr, 1000);
    }
}

void nxworld_main(Handle nxworld_thread) {
    early_init(nxworld_thread);
    serve();
}
