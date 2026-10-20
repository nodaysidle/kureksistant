#!/usr/bin/env bash
# Install Kurek on Arch Linux / Omarchy Quattro
# Idempotent: safe to re-run. Does not clobber .env, config/api_keys.json, or memories.

set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: ./install_linux.sh [--no-enable] [--dev] [--locked]

  --no-enable   Install/render the systemd user unit but do not enable or start it.
  --dev         Also install dev/CI tools from requirements-dev.txt (pytest, ruff, mypy).
  --locked      Install runtime deps from requirements.lock (exact pins) instead of
                requirements.txt.
USAGE
}

ENABLE_UNIT=1
INSTALL_DEV=0
USE_LOCK=0
for arg in "$@"; do
    case "$arg" in
        --no-enable) ENABLE_UNIT=0 ;;
        --dev) INSTALL_DEV=1 ;;
        --locked) USE_LOCK=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; usage >&2; exit 2 ;;
    esac
done

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "=== Installing Kurek on Linux ==="

# --- Helpers ---------------------------------------------------------------

render_unit() {
    # Fill @KUREK_DIR@ placeholders in the repo unit template.
    local src="$1"
    local dest="$2"
    local install_dir="$3"
    sed -e "s|@KUREK_DIR@|${install_dir}|g" "$src" > "$dest"
}

# --- 0. Optional Arch package check (never auto-sudo) ----------------------

REQUIRED_PKGS=(mpv grim wl-clipboard libnotify gcc)
MISSING_PKGS=()

if command -v pacman >/dev/null 2>&1; then
    for pkg in "${REQUIRED_PKGS[@]}"; do
        if ! pacman -Q "$pkg" >/dev/null 2>&1; then
            MISSING_PKGS+=("$pkg")
        fi
    done
    if [ "${#MISSING_PKGS[@]}" -gt 0 ]; then
        echo "⚠️  Missing Arch packages: ${MISSING_PKGS[*]}"
        echo "    Install with: sudo pacman -S --needed ${MISSING_PKGS[*]}"
    fi
else
    # Non-Arch: still require the binaries we can detect without pacman.
    for cmd in mpv grim wl-copy notify-send gcc; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            echo "⚠️  Missing command: $cmd (install via your distro package manager)"
        fi
    done
fi

if ! command -v gcc >/dev/null 2>&1; then
    echo "❌ ERROR: gcc is required to build bin/kurek-trigger (Hyprland bind client)." >&2
    if command -v pacman >/dev/null 2>&1; then
        echo "    Install with: sudo pacman -S --needed gcc" >&2
    fi
    exit 1
fi

# --- 1. Virtualenv + Python deps (always refresh requirements) -------------

if [ ! -d "$DIR/.venv" ]; then
    echo "Creating virtual environment..."
    if command -v uv >/dev/null 2>&1; then
        uv venv .venv --python 3.12
    else
        python3 -m venv .venv
    fi
fi

pip_install() {
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python .venv/bin/python "$@"
    else
        .venv/bin/pip install "$@"
    fi
}

RUNTIME_REQS="requirements.txt"
if [ "$USE_LOCK" -eq 1 ]; then
    if [ ! -f "$DIR/requirements.lock" ]; then
        echo "❌ ERROR: --locked given but requirements.lock is missing." >&2
        exit 1
    fi
    RUNTIME_REQS="requirements.lock"
fi

echo "Installing runtime Python requirements ($RUNTIME_REQS) into .venv..."
pip_install -r "$RUNTIME_REQS"

if [ "$INSTALL_DEV" -eq 1 ]; then
    echo "Installing dev requirements (requirements-dev.txt) into .venv..."
    pip_install -r requirements-dev.txt
fi

echo "Installing Playwright Chromium browser..."
.venv/bin/python -m playwright install chromium

# --- 2. Record installation path (overwrite is intentional / idempotent) ---

CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
mkdir -p "$CONFIG_HOME/kurek"
echo "$DIR" > "$CONFIG_HOME/kurek/install_path"
echo "📝 Wrote install path → $CONFIG_HOME/kurek/install_path"

# --- 3. Setup default .env if missing (never clobber existing) -------------

if [ ! -f "$DIR/.env" ] && [ -f "$DIR/.env.example" ]; then
    cp "$DIR/.env.example" "$DIR/.env"
    echo "⚠️  Created default .env from .env.example. Add your API keys before starting."
fi

# Never touch config/api_keys.json or memory stores.

# --- 4. Install desktop icon (overwrite icons only) ------------------------

mkdir -p "$HOME/.local/share/icons/hicolor/512x512/apps"
cp "$DIR/desktop/kurek.png" "$HOME/.local/share/icons/kurek.png"
cp "$DIR/desktop/kurek.png" "$HOME/.local/share/icons/hicolor/512x512/apps/kurek.png"

# --- 5. Compile C trigger + install CLI (stable ~/.local/bin paths) --------

mkdir -p "$HOME/.local/bin"
cp "$DIR/bin/kurek" "$HOME/.local/bin/kurek"
chmod +x "$HOME/.local/bin/kurek"

echo "Compiling kurek-trigger with -Wall -Wextra..."
gcc -O3 -Wall -Wextra "$DIR/bin/kurek-trigger.c" -o "$HOME/.local/bin/kurek-trigger"
chmod +x "$HOME/.local/bin/kurek-trigger"
echo "⚡ Installed kurek-trigger → $HOME/.local/bin/kurek-trigger"

# --- 6. Render + install systemd user unit from template -------------------

SYSTEMD_USER_DIR="$CONFIG_HOME/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"
render_unit "$DIR/desktop/kurek.service" "$SYSTEMD_USER_DIR/kurek.service" "$DIR"
echo "📦 Installed kurek.service → $SYSTEMD_USER_DIR/kurek.service (KUREK_DIR=$DIR)"

if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    if ! systemctl --user daemon-reload; then
        echo "⚠️  systemctl --user daemon-reload failed; run it manually."
    fi
    if [ "$ENABLE_UNIT" -eq 1 ]; then
        WAS_ACTIVE=0
        if systemctl --user is-active --quiet kurek.service; then
            WAS_ACTIVE=1
        fi
        if systemctl --user enable --now kurek.service; then
            echo "🚀 kurek.service enabled and started (systemctl --user status kurek.service)"
            if [ "$WAS_ACTIVE" -eq 1 ]; then
                echo "ℹ️  kurek.service was already running; restart it to pick up changes:"
                echo "    systemctl --user restart kurek.service"
            fi
        else
            echo "⚠️  Could not enable/start kurek.service. Try manually:"
            echo "    systemctl --user enable --now kurek.service"
            echo "    journalctl --user -u kurek.service -n 50"
        fi
    else
        echo "ℹ️  --no-enable: unit installed but not enabled. Enable later with:"
        echo "    systemctl --user enable --now kurek.service"
    fi
else
    echo "⚠️  systemd user session not available; kurek.service was not enabled."
    echo "    Start Kurek with: kurek start   (falls back to ./launch_kurek.sh)"
fi

# --- 7. Install Desktop Entry (overwrite; no duplicate appends) ------------

mkdir -p "$HOME/.local/share/applications"
sed -e "s|Exec=kurek toggle|Exec=$HOME/.local/bin/kurek toggle|g" \
    "$DIR/desktop/kurek.desktop" > "$HOME/.local/share/applications/kurek.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "✅ Kurek installed successfully!"
echo "👉 Launch via app launcher (Super+Space) -> 'Kurek'"
echo "👉 Or run from terminal: kurek toggle"
echo "👉 Hyprland bind should call: ~/.local/bin/kurek-trigger toggle"
echo "👉 Health check: kurek doctor   ·   Logs: kurek logs -f"
