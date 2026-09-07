#define _POSIX_C_SOURCE 200809L
#include <ucp/api/ucp.h>
#include <ucs/type/status.h>
#include <arpa/inet.h>
#include <errno.h>
#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* The socket is only bootstrap. All measured payloads go through UCP. */
static ucp_worker_h worker;
static ucs_status_t peer_status = UCS_OK;
static unsigned allocated, completed, freed, cancelled;
static int rank_id;
static int trace_enabled = 1;
static uint64_t now_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}
static void event(const char *name, ucs_status_t status) {
    if (!trace_enabled) return;
    fprintf(stderr, "{\"event\":\"%s\",\"rank\":%d,\"time_ns\":%" PRIu64
            ",\"status\":\"%s\"}\n", name, rank_id, now_ns(), ucs_status_string(status));
}
static void die(const char *what) { perror(what); exit(2); }
static void check(ucs_status_t s, const char *what) {
    if (s != UCS_OK) { fprintf(stderr, "%s: %s\n", what, ucs_status_string(s)); exit(2); }
}
static void peer_error(void *arg, ucp_ep_h ep, ucs_status_t s) {
    (void)arg; (void)ep; peer_status = s; event("peer_error", s);
}
static void transfer(int fd, void *buf, size_t len, int writing, size_t fragment) {
    size_t off = 0;
    while (off < len) {
        size_t n = len - off < fragment ? len - off : fragment;
        ssize_t got = writing ? write(fd, (char *)buf + off, n) : read(fd, (char *)buf + off, n);
        if (got < 0 && errno == EINTR) continue;
        if (got <= 0) die("bootstrap I/O");
        off += (size_t)got;
    }
}
static void barrier(int fd) {
    char c = 'B'; transfer(fd, &c, 1, 1, 1); transfer(fd, &c, 1, 0, 1);
}
static ucs_status_t finish(void *req, double timeout_s, int cancel_now) {
    if (UCS_PTR_IS_ERR(req)) { event("immediate_error", UCS_PTR_STATUS(req)); return UCS_PTR_STATUS(req); }
    if (req == NULL) return UCS_OK;  /* Immediate success has no request to free. */
    ++allocated; event("request_owned", UCS_INPROGRESS);
    uint64_t deadline = now_ns() + (uint64_t)(timeout_s * 1e9);
    int did_cancel = 0;
    ucs_status_t s;
    while ((s = ucp_request_check_status(req)) == UCS_INPROGRESS) {
        ucp_worker_progress(worker);
        if (!did_cancel && (cancel_now || now_ns() >= deadline || peer_status != UCS_OK)) {
            ucp_request_cancel(worker, req);
            ++cancelled; did_cancel = 1;
            event("cancel_requested", UCS_INPROGRESS);
            deadline = now_ns() + 2000000000ull;
        } else if (did_cancel && now_ns() >= deadline) {
            event("unresolved_request_fatal", UCS_INPROGRESS);
            /* Never free an in-flight request/buffer. Supervisor treats this as failure. */
            _Exit(3);
        }
    }
    ++completed; event("request_complete", s);
    ucp_request_free(req); ++freed; event("request_freed", s);
    return s;
}
static uint8_t pattern(size_t index, unsigned seq, int owner) {
    uint32_t x = (uint32_t)index * 2654435761u ^ seq * 2246822519u ^ (uint32_t)(owner + 1) * 3266489917u;
    return (uint8_t)(x ^ (x >> 8) ^ (x >> 16) ^ (x >> 24));
}
static size_t number(const char *s, size_t low, size_t high) {
    char *end; errno = 0; unsigned long long v = strtoull(s, &end, 10);
    if (errno || !*s || *end || s[0] == '-' || v < low || v > high) { fprintf(stderr,"invalid integer: %s\n",s); exit(2); }
    return (size_t)v;
}
int main(int argc, char **argv) {
    if (argc != 9) {
        fprintf(stderr, "Usage: clinic rank fd bytes iterations warmup fragment timeout_ms normal|cancel|peer-exit\n"); return 2;
    }
    trace_enabled = !getenv("CLINIC_TRACE") || strcmp(getenv("CLINIC_TRACE"), "0");
    rank_id = (int)number(argv[1], 0, 1);
    int fd = (int)number(argv[2], 0, 1048576);
    size_t bytes = number(argv[3], 1, 64u * 1024u * 1024u);
    unsigned iters = (unsigned)number(argv[4], 1, 100000);
    unsigned warmup = (unsigned)number(argv[5], 0, 10000);
    size_t fragment = number(argv[6], 1, 1048576);
    double timeout = (double)number(argv[7], 1, 60000) / 1000.;
    const char *mode = argv[8];
    if (strcmp(mode,"normal") && strcmp(mode,"cancel") && strcmp(mode,"peer-exit")) return 2;
    signal(SIGPIPE, SIG_IGN);
    ucp_config_t *config; ucp_context_h context; ucp_ep_h ep;
    check(ucp_config_read(NULL, NULL, &config), "config");
    ucp_params_t cp = {.field_mask = UCP_PARAM_FIELD_FEATURES, .features = UCP_FEATURE_TAG};
    check(ucp_init(&cp, config, &context), "init"); ucp_config_release(config);
    ucp_worker_params_t wp = {.field_mask = UCP_WORKER_PARAM_FIELD_THREAD_MODE, .thread_mode = UCS_THREAD_MODE_SINGLE};
    check(ucp_worker_create(context, &wp, &worker), "worker");
    ucp_address_t *local; size_t local_len;
    check(ucp_worker_get_address(worker, &local, &local_len), "address");
    if (local_len > 1048576) return 2;
    uint32_t own_len = htonl((uint32_t)local_len), remote_len = 0;
    /* Rank ordering also works with a one-byte bootstrap fragment. */
    void *remote = NULL;
    for (int sender = 0; sender < 2; ++sender) {
        if (rank_id == sender) {
            transfer(fd, &own_len, sizeof(own_len), 1, fragment);
            transfer(fd, local, local_len, 1, fragment);
        } else {
            transfer(fd, &remote_len, sizeof(remote_len), 0, fragment);
            remote_len = ntohl(remote_len);
            if (!remote_len || remote_len > 1048576) return 2;
            remote = malloc(remote_len); if (!remote) die("malloc");
            transfer(fd, remote, remote_len, 0, fragment);
        }
    }
    ucp_ep_params_t ep_params = {
        .field_mask = UCP_EP_PARAM_FIELD_REMOTE_ADDRESS | UCP_EP_PARAM_FIELD_ERR_HANDLING_MODE | UCP_EP_PARAM_FIELD_ERR_HANDLER,
        .address = remote, .err_mode = UCP_ERR_HANDLING_MODE_PEER,
        .err_handler = {.cb = peer_error, .arg = NULL}};
    check(ucp_ep_create(worker, &ep_params, &ep), "endpoint");
    free(remote); ucp_worker_release_address(worker, local);
    ucp_ep_print_info(ep, stderr);
    uint8_t *tx = malloc(bytes), *rx = malloc(bytes);
    if (!tx || !rx) die("buffers");
    ucp_request_param_t param = {0};
    int ok = 1;
    barrier(fd);
    if (!strcmp(mode, "peer-exit") && rank_id == 1) {
        event("controlled_peer_exit", UCS_OK); _Exit(42);
    }
    if (strcmp(mode, "normal")) {
        void *request = ucp_tag_recv_nbx(worker, rx, bytes, 0xdeadbeef, UINT64_MAX, &param);
        ucs_status_t s = finish(request, timeout, !strcmp(mode, "cancel"));
        ok = (s == UCS_ERR_CANCELED);
    } else {
        for (unsigned seq = 0; seq < warmup + iters && ok; ++seq) {
            for (size_t j = 0; j < bytes; ++j) tx[j] = pattern(j, seq, rank_id);
            memset(rx, 0xa5, bytes);
            uint64_t start = now_ns();
            for (int step = 0; step < 2 && ok; ++step) {
                int send = (step == rank_id);
                void *request = send ? ucp_tag_send_nbx(ep, tx, bytes, 0xabc, &param)
                                     : ucp_tag_recv_nbx(worker, rx, bytes, 0xabc, UINT64_MAX, &param);
                ok = finish(request, timeout, 0) == UCS_OK;
            }
            uint64_t elapsed = now_ns() - start;
            for (size_t j = 0; j < bytes && ok; ++j) if (rx[j] != pattern(j, seq, rank_id ^ 1)) {
                fprintf(stderr, "payload mismatch seq=%u byte=%zu\n", seq, j); ok = 0;
            }
            if (seq >= warmup && ok && rank_id == 0)
                printf("{\"sample\":%u,\"roundtrip_ns\":%" PRIu64 ",\"bytes\":%zu}\n", seq-warmup, elapsed, bytes);
        }
    }
    if (strcmp(mode, "peer-exit")) barrier(fd);
    ucp_request_param_t close_param = {.op_attr_mask = UCP_OP_ATTR_FIELD_FLAGS,
                                      .flags = UCP_EP_CLOSE_FLAG_FORCE};
    check(finish(ucp_ep_close_nbx(ep, &close_param), timeout, 0), "close");
    free(tx); free(rx); close(fd);
    ucp_worker_destroy(worker); ucp_cleanup(context);
    ok = ok && allocated == completed && completed == freed;
    printf("{\"result\":\"%s\",\"rank\":%d,\"allocated\":%u,\"completed\":%u,\"freed\":%u,\"cancelled\":%u}\n",
           ok ? "pass" : "fail", rank_id, allocated, completed, freed, cancelled);
    return ok ? 0 : 1;
}
