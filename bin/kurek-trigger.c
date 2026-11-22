/*
 * bin/kurek-trigger.c — Ultra-low latency C trigger client for Kurek UDS socket.
 * Dispatches JSON over $XDG_RUNTIME_DIR/kurek.sock (or a private per-user path).
 * Fallback launcher path is read from $XDG_CONFIG_HOME/kurek/install_path
 * (default: ~/.config/kurek/install_path).
 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

#define CONNECT_TIMEOUT_MS 5000
#define CONNECT_POLL_MS 100
#define IO_TIMEOUT_USEC 500000
#define MAX_PROMPT_BYTES (64 * 1024) /* the daemon's HTTP body cap: longer is refused, never cut */

/* Escape src into dest as a JSON string body (no surrounding quotes).
 * Returns 0 on success, -1 if dest is too small.
 */
static int json_escape(const char *src, char *dest, size_t dest_size) {
    size_t j = 0;
    for (size_t i = 0; src[i] != '\0'; i++) {
        unsigned char c = (unsigned char)src[i];
        char tmp[8];
        const char *esc = NULL;

        if (c == '"') {
            esc = "\\\"";
        } else if (c == '\\') {
            esc = "\\\\";
        } else if (c == '\b') {
            esc = "\\b";
        } else if (c == '\f') {
            esc = "\\f";
        } else if (c == '\n') {
            esc = "\\n";
        } else if (c == '\r') {
            esc = "\\r";
        } else if (c == '\t') {
            esc = "\\t";
        } else if (c < 0x20) {
            int n = snprintf(tmp, sizeof(tmp), "\\u%04x", (unsigned int)c);
            if (n < 0 || (size_t)n >= sizeof(tmp)) {
                return -1;
            }
            esc = tmp;
        }

        if (esc != NULL) {
            size_t elen = strlen(esc);
            if (j + elen >= dest_size) {
                return -1;
            }
            memcpy(dest + j, esc, elen);
            j += elen;
        } else {
            if (j + 1 >= dest_size) {
                return -1;
            }
            dest[j++] = (char)c;
        }
    }
    if (j >= dest_size) {
        return -1;
    }
    dest[j] = '\0';
    return 0;
}

static void trim_trailing_ws(char *s) {
    size_t n = strlen(s);
    while (n > 0) {
        char c = s[n - 1];
        if (c == '\n' || c == '\r' || c == ' ' || c == '\t') {
            s[--n] = '\0';
        } else {
            break;
        }
    }
}

/* Resolve install dir written by install_linux.sh. Honours $XDG_CONFIG_HOME. */
static int read_install_path(char *dest, size_t max_len) {
    char path[512];
    const char *xdg_config = getenv("XDG_CONFIG_HOME");

    if (xdg_config != NULL && xdg_config[0] != '\0') {
        if (snprintf(path, sizeof(path), "%s/kurek/install_path", xdg_config) >= (int)sizeof(path)) {
            return 0;
        }
    } else {
        const char *home = getenv("HOME");
        if (home == NULL || home[0] == '\0') {
            return 0;
        }
        if (snprintf(path, sizeof(path), "%s/.config/kurek/install_path", home) >= (int)sizeof(path)) {
            return 0;
        }
    }

    FILE *f = fopen(path, "r");
    if (f == NULL) {
        return 0;
    }
    if (fgets(dest, (int)max_len, f) == NULL) {
        fclose(f);
        return 0;
    }
    fclose(f);
    trim_trailing_ws(dest);
    return dest[0] != '\0';
}

/* Match daemon path resolution: XDG_RUNTIME_DIR, else private ~/.cache/kurek/run. */
static int get_sock_path(char *dest, size_t max_len) {
    const char *xdg = getenv("XDG_RUNTIME_DIR");
    if (xdg != NULL && xdg[0] != '\0') {
        if (snprintf(dest, max_len, "%s/kurek.sock", xdg) >= (int)max_len) {
            return -1;
        }
        return 0;
    }

    const char *home = getenv("HOME");
    if (home == NULL || home[0] == '\0') {
        fprintf(stderr, "kurek-trigger: XDG_RUNTIME_DIR unset and HOME unavailable\n");
        return -1;
    }
    if (snprintf(dest, max_len, "%s/.cache/kurek/run/kurek.sock", home) >= (int)max_len) {
        return -1;
    }
    return 0;
}

static void set_io_timeouts(int fd) {
    struct timeval tv;
    tv.tv_sec = 0;
    tv.tv_usec = IO_TIMEOUT_USEC;
    (void)setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    (void)setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
}

static int fill_addr(struct sockaddr_un *addr, const char *sock_path) {
    size_t path_len = strlen(sock_path);
    if (path_len >= sizeof(addr->sun_path)) {
        fprintf(stderr, "kurek-trigger: socket path too long\n");
        return -1;
    }
    memset(addr, 0, sizeof(*addr));
    addr->sun_family = AF_UNIX;
    memcpy(addr->sun_path, sock_path, path_len + 1);
    return 0;
}

static int connect_once(const struct sockaddr_un *addr) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    set_io_timeouts(fd);
    if (connect(fd, (const struct sockaddr *)addr, sizeof(*addr)) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

/* Poll until the socket accepts, or total timeout elapses. */
static int connect_with_poll(const struct sockaddr_un *addr, int timeout_ms) {
    int elapsed = 0;
    while (1) {
        int fd = connect_once(addr);
        if (fd >= 0) {
            return fd;
        }
        if (elapsed >= timeout_ms) {
            break;
        }
        usleep((useconds_t)CONNECT_POLL_MS * 1000U);
        elapsed += CONNECT_POLL_MS;
    }
    return -1;
}

static void try_start_daemon(void) {
    pid_t pid = fork();
    if (pid == 0) {
        execlp("systemctl", "systemctl", "--user", "start", "kurek.service", (char *)NULL);
        _exit(127);
    }
    if (pid > 0) {
        int status = 0;
        if (waitpid(pid, &status, 0) >= 0 && WIFEXITED(status) && WEXITSTATUS(status) == 0) {
            return;
        }
    }

    char install_path[512];
    if (!read_install_path(install_path, sizeof(install_path))) {
        return;
    }

    char script[768];
    if (snprintf(script, sizeof(script), "%s/launch_kurek.sh", install_path) >= (int)sizeof(script)) {
        return;
    }
    if (access(script, X_OK) != 0) {
        return;
    }

    pid = fork();
    if (pid == 0) {
        /* Double-fork so the long-running launcher is reparented. */
        pid_t child = fork();
        if (child == 0) {
            (void)setsid();
            int devnull = open("/dev/null", O_RDWR);
            if (devnull >= 0) {
                (void)dup2(devnull, STDIN_FILENO);
                (void)dup2(devnull, STDOUT_FILENO);
                (void)dup2(devnull, STDERR_FILENO);
                if (devnull > 2) {
                    close(devnull);
                }
            }
            execl(script, script, (char *)NULL);
            _exit(127);
        }
        _exit(child < 0 ? 127 : 0);
    }
    if (pid > 0) {
        (void)waitpid(pid, NULL, 0);
    }
}

/* Build the JSON line for the daemon in a malloc'd buffer (caller frees). The prompt (args joined by
 * one space) is never cut: over MAX_PROMPT_BYTES, or on any failure, NULL. *prompt_len gets its length.
 */
static char *build_message(const char *action, int argc, char **argv, size_t *prompt_len) {
    char action_esc[256];
    *prompt_len = 0;
    if (json_escape(action, action_esc, sizeof(action_esc)) != 0) {
        return NULL;
    }

    if (strcmp(action, "prompt") == 0 && argc >= 3) {
        size_t len = 0;
        for (int i = 2; i < argc; i++) {
            len += strlen(argv[i]) + (i > 2 ? 1 : 0);
        }
        *prompt_len = len;
        if (len > MAX_PROMPT_BYTES) {
            return NULL;
        }

        char *joined = malloc(len + 1);
        if (joined == NULL) {
            return NULL;
        }
        size_t offset = 0;
        for (int i = 2; i < argc; i++) {
            size_t arg_len = strlen(argv[i]);
            if (offset > 0) {
                joined[offset++] = ' ';
            }
            memcpy(joined + offset, argv[i], arg_len);
            offset += arg_len;
        }
        joined[offset] = '\0';

        size_t esc_size = len * 6 + 1; /* worst case: every byte becomes \u00XX */
        char *prompt_esc = malloc(esc_size);
        if (prompt_esc == NULL || json_escape(joined, prompt_esc, esc_size) != 0) {
            free(prompt_esc);
            free(joined);
            return NULL;
        }
        free(joined);

        size_t msg_size = len * 6 + strlen(action_esc) + 32;
        char *msg = malloc(msg_size);
        if (msg == NULL ||
            snprintf(msg, msg_size, "{\"action\":\"%s\",\"prompt\":\"%s\"}\n", action_esc, prompt_esc) >= (int)msg_size) {
            free(msg);
            free(prompt_esc);
            return NULL;
        }
        free(prompt_esc);
        return msg;
    }

    size_t msg_size = strlen(action_esc) + 16;
    char *msg = malloc(msg_size);
    if (msg == NULL || snprintf(msg, msg_size, "{\"action\":\"%s\"}\n", action_esc) >= (int)msg_size) {
        free(msg);
        return NULL;
    }
    return msg;
}

static int write_all(int fd, const char *buf, size_t len) {
    while (len > 0) {
        ssize_t n = write(fd, buf, len);
        if (n <= 0) {
            if (n < 0 && errno == EINTR) {
                continue;
            }
            return -1;
        }
        buf += n;
        len -= (size_t)n;
    }
    return 0;
}

int main(int argc, char *argv[]) {
    const char *action = "toggle";
    if (argc >= 2) {
        action = argv[1];
    }

    /* Built before connecting: a prompt that cannot be sent whole never starts or reaches the daemon. */
    size_t prompt_len = 0;
    char *msg = build_message(action, argc, argv, &prompt_len);
    if (msg == NULL) {
        if (prompt_len > MAX_PROMPT_BYTES) {
            fprintf(stderr, "kurek-trigger: prompt not sent: it is %zu bytes, over the %d-byte limit\n",
                    prompt_len, MAX_PROMPT_BYTES);
            return 5;
        }
        return 3;
    }

    char sock_path[512];
    if (get_sock_path(sock_path, sizeof(sock_path)) != 0) {
        free(msg);
        return 1;
    }

    struct sockaddr_un addr;
    if (fill_addr(&addr, sock_path) != 0) {
        free(msg);
        return 1;
    }

    int fd = connect_once(&addr);
    if (fd < 0) {
        try_start_daemon();
        fd = connect_with_poll(&addr, CONNECT_TIMEOUT_MS);
        if (fd < 0) {
            free(msg);
            return 2;
        }
    }

    if (write_all(fd, msg, strlen(msg)) != 0) {
        free(msg);
        close(fd);
        return 4;
    }
    free(msg);

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
