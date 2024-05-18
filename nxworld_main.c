#include <switch.h>
#include "nxworld_main.h"

void virtmemSetup(void);
void newlibSetup(void);
void __libnx_init_thread(void);

int __nx_applet_type = -2;

void userAppExit() {
}

void __nx_exit() {
    log_str("__nx_exit");
    while (1) {}
}

static const SocketInitConfig socket_init_config = {
    .tcp_tx_buf_size        = 0x10000,
    .tcp_rx_buf_size        = 0x10000,
    .tcp_tx_buf_max_size    = 0x10000,
    .tcp_rx_buf_max_size    = 0x10000,

    .udp_tx_buf_size = 0,
    .udp_rx_buf_size = 0,

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

static void mongoose_callback(struct mg_connection *c, int ev, void *ev_data) {
    if (ev == MG_EV_HTTP_MSG) {  // New HTTP request received
        struct mg_http_message *hm = (struct mg_http_message *) ev_data;
        if (mg_match(hm->uri, mg_str("/api/hello"), NULL)) {
            mg_http_reply(c, 200, "", "{%m:%d}\n", MG_ESC("status"), 1);
        } else {
            mg_http_reply(c, 404, "", "not found");
        }
    }
}

static void serve() {
    struct mg_mgr mgr;
    mg_mgr_init(&mgr);
    mg_http_listen(&mgr, "http://0.0.0.0:8000", mongoose_callback, NULL);
    while (1) {
        mg_mgr_poll(&mgr, 1000);
    }
}

void nxworld_main(Handle nxworld_thread) {
    early_init(nxworld_thread);
    serve();
}
