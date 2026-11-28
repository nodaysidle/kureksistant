<div align="center">
  <img src="desktop/kurek.png" width="128" alt="Kureksistant icon">
  <h1>Kureksistant</h1>
  <p><strong>A headless voice assistant for Arch Linux and Hyprland that acts on your desktop.</strong></p>
  <p>Summon it with a middle-click or a typed prompt, ask for something, and hear the answer. It has no GUI on purpose.</p>

  <p>
    <a href="https://github.com/nodaysidle/kureksistant/releases/tag/v0.2.6"><img src="https://img.shields.io/badge/release-v0.2.6-4c8c6b?style=flat-square" alt="Release v0.2.6"></a>
    <img src="https://img.shields.io/badge/Linux-Arch%20%2F%20Omarchy%20%2F%20Hyprland-1793D1?style=flat-square&logo=arch-linux&logoColor=white" alt="Arch / Omarchy / Hyprland">
    <img src="https://img.shields.io/badge/Android-Kurek%20Remote%200.2.6-3DDC84?style=flat-square&logo=android&logoColor=white" alt="Kurek Remote 0.2.6">
    <img src="https://img.shields.io/badge/license-CC%20BY--NC%204.0-lightgrey?style=flat-square" alt="CC BY-NC 4.0">
  </p>
</div>

---

Kurek runs in the background as a systemd user service. When you summon it, it listens, transcribes what you said, thinks with DeepSeek, acts through tool modules in [`actions/`](actions/), and speaks the answer. Kurek Remote, an Android app, can send it prompts over Tailscale.

Kurek is for someone who runs Arch Linux with Hyprland and wants to control windows, files and small chores by voice. The installer is written for Arch-family systems only.

## How it works

```text
Middle-click / SUPER+SHIFT+K / kurek CLI / Kurek Remote
  -> listen       voice activity detection with room calibration, or plain text
  -> transcribe   OpenRouter Whisper or Deepgram Nova-2 when a key is set, else local faster-whisper
  -> think        DeepSeek-Flash, with your rules, memory ledger and recent chat
  -> act          28 tool modules in actions/
  -> speak        xAI Grok voice Sol, then OpenRouter, then Gemini, then spd-say
```

## What you can say

- "Make this window fullscreen."
- "Turn the volume up."
- "What is the weather tomorrow?"
- "Remind me in twenty minutes to stretch."
- "Search the web for the latest Python release."
- "Find the budget spreadsheet in my Documents folder."
- "Show the git status of my projects."
- "Check the entrance camera." (needs the camera watcher, see below)

## Requirements

- Arch Linux or an Arch-based distribution, with Hyprland on Wayland. Omarchy is the setup the installer targets.
- PipeWire, `mpv`, `grim`, `wl-clipboard`, `libnotify` and `gcc`. The installer checks for these.
- Python 3.10 or newer (`pyproject.toml`).
- A microphone.
- Keys: `DEEPSEEK_API_KEY` and `XAI_API_KEY` are needed for the default setup. `GEMINI_API_KEY` is recommended for screen and camera vision. `DEEPGRAM_API_KEY` and `OPENROUTER_API_KEY` are optional.
- Without cloud keys, speech recognition runs locally. Speech output then falls back to `spd-say`.

## Install

1. Clone the repository: `git clone https://github.com/nodaysidle/kureksistant.git && cd kureksistant`.
2. Run `./install_linux.sh`. It creates `.venv`, installs dependencies, and sets up the `kurek.service` user unit.
3. Add your keys to `.env`, which the installer creates from `.env.example`.
4. Run `kurek doctor` and fix anything it reports.
5. Add these Hyprland bindings, then reload Hyprland:

```lua
-- ~/.config/hypr/bindings.lua
o.bind("mouse:274", "Summon Kurek Middle Click", "~/.local/bin/kurek-trigger toggle", { mouse = true })
o.bind("SUPER + mouse:274", "Summon Kurek Super+Middle Click", "~/.local/bin/kurek-trigger toggle", { mouse = true })
o.bind("SUPER + SHIFT + K", "Type to Kurek", "uwsm-app -- xdg-terminal-exec --app-id=com.nodaysidle.kurek-ask --title=Kurek -e ~/.local/bin/kurek-ask")
o.window({ class = "^com.nodaysidle.kurek-ask$" }, { float = true, center = true, size = { 640, 90 } })
```

Flags, what the installer changes, and the troubleshooting steps are in [docs/INSTALL.md](docs/INSTALL.md).

## Summon it

Middle-click starts listening, and a second middle-click stops and sends. `SUPER + SHIFT + K` opens a small typed prompt for names the microphone gets wrong. The `kurek` CLI and the Kurek Remote app do the same job. Kurek is built not to cut you off while you are speaking or while a reply plays. Details are in [docs/SUMMON.md](docs/SUMMON.md) and [docs/FEATURES.md](docs/FEATURES.md).

## Memory and rules you control

Your rules live in a file you write by hand, and Kurek never edits it. What Kurek learns goes to an append-only ledger, and a correction supersedes an old entry without deleting it. Kurek proposes rules but never applies one until you copy it in. The full list of stores and their write rules is in [docs/ANTI-DRIFT.md](docs/ANTI-DRIFT.md).

## Phone remote

Kurek Remote can send prompts to the daemon over Tailscale. Remote access is off by default, so the daemon listens on loopback only until you set a token and a bind mode. Setup and the security settings are in [docs/REMOTE.md](docs/REMOTE.md).

## Camera watcher

An opt-in watcher reads a local RTSP camera, detects vehicles in a region you set, and alerts once when one arrives. Detection runs on this machine with `onnxruntime` on the CPU. Setup is in [docs/CAMERA.md](docs/CAMERA.md).

## Documentation

- [docs/INSTALL.md](docs/INSTALL.md): installer steps, flags, service unit, troubleshooting
- [docs/SUMMON.md](docs/SUMMON.md): every way to start Kurek, and the `kurek` CLI
- [docs/FEATURES.md](docs/FEATURES.md): what the tool modules do, speech timing, memory priority
- [docs/REFERENCE.md](docs/REFERENCE.md): full `.env` configuration, file paths, security model, stack
- [docs/ANTI-DRIFT.md](docs/ANTI-DRIFT.md): memory invariants and rule-following evaluation
- [docs/GLOSSARY.md](docs/GLOSSARY.md): canonical names for every memory store
- [docs/REMOTE.md](docs/REMOTE.md): Kurek Remote pairing, phone settings and live voice
- [docs/CAMERA.md](docs/CAMERA.md): RTSP camera watcher configuration and behaviour
- [.github/SECURITY.md](.github/SECURITY.md): security policy and vulnerability reporting
- [CHANGELOG.md](CHANGELOG.md): release history

## License

[CC BY-NC 4.0](LICENSE) (`SPDX-License-Identifier: CC-BY-NC-4.0`), derived from FatihMakes' JARVIS ("MARK 53"). Kureksistant changes by [NODAYSIDLE](https://github.com/nodaysidle). See [NOTICE](NOTICE).
