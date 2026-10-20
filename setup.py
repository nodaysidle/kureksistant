#!/usr/bin/env python3
"""
Kureksistant bootstrap helper — headless Linux daemon only.

This is NOT a setuptools script: Kurek is not an installable package, so
`pip install -e .` is unsupported. Runtime dependencies live in
requirements.txt (exact pins: requirements.lock). On Arch / Omarchy prefer
`./install_linux.sh`, which also builds the trigger and installs the systemd unit.

Usage: python3 setup.py      # pip-installs requirements.txt into this interpreter
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    if len(sys.argv) > 1:
        # Invoked by a build backend (egg_info, develop, bdist_wheel, ...).
        sys.exit(
            "setup.py is a bootstrap helper, not a package build script. "
            "Use ./install_linux.sh or `pip install -r requirements.txt`."
        )

    print("\n▶ Installing daemon Python dependencies (requirements.txt)…")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")],
        check=True,
    )

    print(
        "\nℹ️  Native tools Kurek uses on Arch / Omarchy:\n"
        "    • audio I/O       → portaudio + mpv\n"
        "    • notifications   → libnotify (notify-send)\n"
        "    • volume          → pipewire-pulse (pactl)\n"
        "    • brightness      → brightnessctl\n"
        "    • Wayland UX      → grim, wl-clipboard, wtype\n"
        "    • open URLs       → xdg-utils (xdg-open)\n"
        "    • browser tools   → python -m playwright install chromium\n"
    )
    print("✅ Setup complete!")
    print("   1) cp .env.example .env && chmod 600 .env   # add DEEPSEEK_API_KEY, XAI_API_KEY")
    print("   2) ./install_linux.sh     # CLI, trigger, desktop entry, systemd unit")
    print("   3) kurek start            # or: ./launch_kurek.sh")
    print("   4) kurek doctor")


if __name__ == "__main__":
    main()
