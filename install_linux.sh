#!/usr/bin/env bash
# Install Kurek on Arch Linux / Omarchy Quattro
# Idempotent: safe to re-run. Does not clobber .env, config/api_keys.json, or memories.

set -euo pipefail

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

echo "Installing Python requirements into .venv..."
if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python -r requirements.txt
else
    .venv/bin/pip install -r requirements.txt
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
# Also keep a copy next to sources for repo-local use.
cp "$HOME/.local/bin/kurek-trigger" "$DIR/bin/kurek-trigger"
echo "⚡ Installed kurek-trigger → $HOME/.local/bin/kurek-trigger"

# --- 6. Render + install systemd user unit from template -------------------

mkdir -p "$HOME/.config/systemd/user"
render_unit "$DIR/desktop/kurek.service" "$HOME/.config/systemd/user/kurek.service" "$DIR"
systemctl --user daemon-reload 2>/dev/null || true
echo "📦 Installed kurek.service → ~/.config/systemd/user/kurek.service (KUREK_DIR=$DIR)"

# --- 7. Install Desktop Entry (overwrite; no duplicate appends) ------------

mkdir -p "$HOME/.local/share/applications"
sed -e "s|Exec=kurek toggle|Exec=$HOME/.local/bin/kurek toggle|g" \
    "$DIR/desktop/kurek.desktop" > "$HOME/.local/share/applications/kurek.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "✅ Kurek installed successfully!"
echo "👉 Launch via app launcher (Super+Space) -> 'Kurek'"
echo "👉 Or run from terminal: kurek toggle"
echo "👉 Hyprland bind should call: ~/.local/bin/kurek-trigger toggle"
