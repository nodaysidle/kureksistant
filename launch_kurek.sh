#!/usr/bin/env bash
# Launch the headless Kurek daemon without systemd (fallback for `kurek start`
# when kurek.service is not enabled). Usage: ./launch_kurek.sh [start|stop|status]

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

# Logs and PID files are private to the user (dir 700, files 600).
umask 077

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/kurek"
LOG_FILE="$STATE_DIR/daemon.log"
LOG_MAX_BYTES=$((5 * 1024 * 1024))
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp}"
DAEMON_PID_FILE="$RUNTIME_DIR/kurek_daemon.pid"
LEGACY_PID_FILE="/tmp/kurek_daemon.pid"
DAEMON_URL="http://127.0.0.1:8790"

case "${1:-start}" in
  stop)
    echo "Stopping Kurek..."
    for pid_file in "$DAEMON_PID_FILE" "$LEGACY_PID_FILE"; do
      if [ -f "$pid_file" ]; then
        kill "$(cat "$pid_file")" 2>/dev/null || true
        rm -f "$pid_file"
      fi
    done
    pkill -f "kurek_daemon.py" 2>/dev/null || true
    echo "Kurek stopped."
    exit 0
    ;;
  status)
    if curl -s "$DAEMON_URL/status" >/dev/null; then
      echo "Kurek daemon is RUNNING: $(curl -s "$DAEMON_URL/status")"
    else
      echo "Kurek daemon is NOT running."
    fi
    exit 0
    ;;
  start|"")
    if systemctl --user is-active --quiet kurek.service 2>/dev/null; then
      echo "Kurek is already running under systemd (kurek.service)."
      echo "👉 Restart: systemctl --user restart kurek.service · Logs: kurek logs -f"
      exit 0
    fi

    echo "=========================================================="
    echo "⚡ LAUNCHING KUREK (headless daemon)"
    echo "=========================================================="

    # Mirror the systemd unit: EnvironmentFile=-.env and one BLAS thread
    # (avoids the numpy/PortAudio import-time deadlock).
    if [ -f "$DIR/.env" ]; then
      set -a
      # shellcheck source=/dev/null
      . "$DIR/.env"
      set +a
    fi
    export OPENBLAS_NUM_THREADS=1

    mkdir -p "$STATE_DIR"
    chmod 700 "$STATE_DIR"
    if [ -f "$LOG_FILE" ] && [ "$(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0)" -gt "$LOG_MAX_BYTES" ]; then
      mv -f "$LOG_FILE" "$LOG_FILE.1"
    fi
    touch "$LOG_FILE"
    chmod 600 "$LOG_FILE"

    # Stop any old instances
    pkill -f "kurek_daemon.py" 2>/dev/null || true
    sleep 0.5

    echo "Starting Kurek daemon on $DAEMON_URL..."
    setsid "$DIR/.venv/bin/python" -u "$DIR/kurek_daemon.py" >> "$LOG_FILE" 2>&1 &
    echo $! > "$DAEMON_PID_FILE"

    # Wait for daemon to respond (up to 8 seconds for cold start)
    for _ in {1..40}; do
      if curl -s "$DAEMON_URL/status" >/dev/null; then
        echo "Daemon is online! State: $(curl -s "$DAEMON_URL/status")"
        break
      fi
      sleep 0.2
    done

    echo ""
    echo "✅ Kurek daemon is LIVE."
    echo "👉 Toggle listening: kurek toggle   (or Middle Click via Hyprland bind)"
    echo "👉 Send prompt:     kurek prompt \"...\""
    echo "👉 Logs: kurek logs -f   ($LOG_FILE)"
    echo "👉 To stop: kurek stop   (or ./launch_kurek.sh stop)"
    ;;
  *)
    echo "Usage: ./launch_kurek.sh [start|stop|status]"
    exit 1
    ;;
esac
