#!/usr/bin/env bash
# Install Kurek on Arch Linux / Omarchy Quattro

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "=== Installing Kurek on Linux ==="

# 1. Ensure Python virtualenv exists
if [ ! -d "$DIR/.venv" ]; then
    echo "Creating virtual environment..."
    if command -v uv >/dev/null 2>&1; then
        uv venv .venv --python 3.12
        uv pip install --python .venv/bin/python -r requirements.txt
    else
        python3 -m venv .venv
        .venv/bin/pip install -r requirements.txt
    fi
fi

# 2. Record installation path
mkdir -p "$HOME/.config/kurek"
echo "$DIR" > "$HOME/.config/kurek/install_path"

# 3. Setup default .env if missing
if [ ! -f "$DIR/.env" ] && [ -f "$DIR/.env.example" ]; then
    cp "$DIR/.env.example" "$DIR/.env"
    echo "⚠️  Created default .env from .env.example. Add your API keys before starting."
fi

# 4. Install desktop icon
mkdir -p "$HOME/.local/share/icons/hicolor/512x512/apps"
cp "$DIR/desktop/kurek.png" "$HOME/.local/share/icons/kurek.png"
cp "$DIR/desktop/kurek.png" "$HOME/.local/share/icons/hicolor/512x512/apps/kurek.png"

# 5. Compile and install CLI and C trigger binary
mkdir -p "$HOME/.local/bin"
cp "$DIR/bin/kurek" "$HOME/.local/bin/kurek"
chmod +x "$HOME/.local/bin/kurek"

if command -v gcc >/dev/null 2>&1 && [ -f "$DIR/bin/kurek-trigger.c" ]; then
    gcc -O3 "$DIR/bin/kurek-trigger.c" -o "$HOME/.local/bin/kurek-trigger"
    chmod +x "$HOME/.local/bin/kurek-trigger"
    echo "⚡ Compiled and installed kurek-trigger (<1ms UDS client)"
fi

# 6. Install systemd user service
mkdir -p "$HOME/.config/systemd/user"
cp "$DIR/desktop/kurek.service" "$HOME/.config/systemd/user/kurek.service"
systemctl --user daemon-reload 2>/dev/null || true
echo "📦 Installed kurek.service to ~/.config/systemd/user/"

# 7. Install Desktop Entry
mkdir -p "$HOME/.local/share/applications"
sed -e "s|Exec=kurek toggle|Exec=$HOME/.local/bin/kurek toggle|g" \
    "$DIR/desktop/kurek.desktop" > "$HOME/.local/share/applications/kurek.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "✅ Kurek installed successfully!"
echo "👉 Launch via app launcher (Super+Space) -> 'Kurek'"
echo "👉 Or run from terminal: kurek toggle"
