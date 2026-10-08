#!/usr/bin/env python3
"""
Kureksistant setup — headless daemon path.

Installs the Python dependencies needed to run `kurek_daemon.py`, then reminds
you to configure `.env` and start the daemon with `kurek start`.

The unsupported PyQt6 JARVIS GUI installer lives at `legacy/setup_gui.py`.
"""
import platform
import subprocess
import sys
from pathlib import Path

OS = platform.system()  # "Windows" | "Darwin" | "Linux"
ROOT = Path(__file__).resolve().parent


def _run(label: str, args: list[str]) -> None:
    print(f"\n▶ {label}")
    subprocess.run(args, check=True)


def main() -> None:
    print(f"⚙  Kureksistant setup — detected OS: {OS or 'unknown'}")

    req = ROOT / "requirements.txt"
    _run(
        "Installing daemon Python dependencies…",
        [sys.executable, "-m", "pip", "install", "-r", str(req)],
    )

    if OS == "Linux":
        print(
            "\nℹ️  Linux note — install native tools you plan to use:\n"
            "    • audio I/O   → portaudio (libportaudio2) + mpv\n"
            "    • notifications → libnotify (notify-send)\n"
            "    • volume      → pulseaudio-utils / pipewire-pulse (pactl)\n"
            "    • brightness  → brightnessctl\n"
            "    • Wayland UX  → grim, wl-clipboard\n"
            "    • open URLs   → xdg-utils (xdg-open)\n"
            "    • pyautogui   → python3-tk\n"
        )
    elif OS == "Darwin":
        print(
            "\nℹ️  macOS note — volume/brightness/reminders use built-in tools.\n"
            "    Optional menu bar: swiftc -O -o menubar/KurekBar menubar/KurekBar.swift\n"
        )

    print("\n✅ Setup complete!")
    print("   1) cp .env.example .env   # then add DEEPSEEK_API_KEY and XAI_API_KEY")
    print("   2) ./install_linux.sh     # optional: CLI + desktop entry (Linux)")
    print("   3) kurek start            # or: ./launch_kurek.sh")
    print("   4) curl -s http://127.0.0.1:8790/status")
    print("\n   Legacy GUI (unsupported): see legacy/README.md")


if __name__ == "__main__":
    main()
