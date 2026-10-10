/*
 * bin/kurek-trigger.c — Ultra-low latency (<1ms) C trigger client for Kurek UDS socket.
 * Dispatches raw JSON over $XDG_RUNTIME_DIR/kurek.sock without Python/Bash overhead.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>

static void get_sock_path(char *dest, size_t max_len) {
    const char *xdg = getenv("XDG_RUNTIME_DIR");
    if (xdg && xdg[0] != '\0') {
        snprintf(dest, max_len, "%s/kurek.sock", xdg);
        return;
    }
    uid_t uid = getuid();
    snprintf(dest, max_len, "/run/user/%d/kurek.sock", (int)uid);
    if (access(dest, F_OK) == 0) {
        return;
    }
    snprintf(dest, max_len, "/tmp/kurek.sock");
}

int main(int argc, char *argv[]) {
    const char *action = "toggle";
    char prompt_buf[4096] = {0};

    if (argc >= 2) {
        action = argv[1];
    }

    char sock_path[512];
    get_sock_path(sock_path, sizeof(sock_path));

    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        return 1;
    }

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, sock_path, sizeof(addr.sun_path) - 1);

    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(fd);
        // Socket offline — try starting daemon in background
        system("systemctl --user start kurek.service >/dev/null 2>&1 || (/home/arch/dev/nodaysidle/kurekizmo/launch_kurek.sh >/dev/null 2>&1 &)");
        usleep(250000); // Wait 250ms

        fd = socket(AF_UNIX, SOCK_STREAM, 0);
        if (fd < 0 || connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
            if (fd >= 0) close(fd);
            return 2;
        }
    }

    char msg[8192];
    if (strcmp(action, "prompt") == 0 && argc >= 3) {
        // Concatenate remaining arguments
        size_t offset = 0;
        for (int i = 2; i < argc; i++) {
            size_t len = strlen(argv[i]);
            if (offset + len + 2 < sizeof(prompt_buf)) {
                if (offset > 0) {
                    prompt_buf[offset++] = ' ';
                }
                memcpy(prompt_buf + offset, argv[i], len);
                offset += len;
            }
        }
        prompt_buf[offset] = '\0';
        snprintf(msg, sizeof(msg), "{\"action\":\"prompt\",\"prompt\":\"%s\"}\n", prompt_buf);
    } else {
        snprintf(msg, sizeof(msg), "{\"action\":\"%s\"}\n", action);
    }

    ssize_t sent = write(fd, msg, strlen(msg));
    (void)sent;

    // Optional fast read for response (non-blocking or brief)
    char resp[512];
    ssize_t n = read(fd, resp, sizeof(resp) - 1);
    if (n > 0) {
        resp[n] = '\0';
        if (strcmp(action, "status") == 0) {
            fputs(resp, stdout);
        }
    }

    close(fd);
    return 0;
}
