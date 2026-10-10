<p align="center">
  <img src="desktop/kurek.png" alt="Kureksistant Icon" width="128" style="border-radius: 24px;" />
</p>

<p align="center">
  <strong>Kureksistant (Kurek)</strong>
</p>

<p align="center">
  <strong>Autonomous personal AI assistant with native screen vision, xAI Grok voice, Wayland clipboard manager, TypeSafe Jev cognition, and Muse Memory. Built for Arch Linux (Hyprland / Omarchy) and macOS. ~450MB peak RAM with faster-whisper loaded (observed).</strong>
</p>

<p align="center">
  <a href="https://github.com/nodaysidle/kureksistant/releases/tag/v0.2.0"><img src="https://img.shields.io/badge/Release-v0.2.0-blue.svg?style=flat-square" alt="Latest Release"></a>
  <img src="https://img.shields.io/badge/Platform-Arch%20Linux%20(Hyprland)%20%7C%20macOS-1793D1?style=flat-square&logo=arch-linux&logoColor=white" alt="Platform">
  <img src="https://img.shields.io/badge/Footprint-~450MB%20peak%20(Whisper)-brightgreen?style=flat-square" alt="Memory">
  <img src="https://img.shields.io/badge/Brain-DeepSeek--Flash-4E6EF2?style=flat-square&logo=deepseek&logoColor=white" alt="DeepSeek">
  <img src="https://img.shields.io/badge/Voice-xAI%20Grok%20(Sol)-1E1E1E?style=flat-square&logo=x&logoColor=white" alt="xAI Grok">
  <img src="https://img.shields.io/badge/Vision-Gemini%20Flash-4285F4?style=flat-square&logo=google&logoColor=white" alt="Gemini Vision">
  <img src="https://img.shields.io/badge/Cognition-TypeSafe%20Jev-FF5722?style=flat-square" alt="TypeSafe Jev">
  <img src="https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey?style=flat-square" alt="License">
</p>

---

> **The Problem:** Modern desktop AI assistants are bloated 500MB+ Electron web apps that hijack workstation RAM, require manual window-switching, and lack direct hardware input integration.
>
> **The Result:** Kureksistant is a headless local daemon (~450MB peak with faster-whisper loaded, observed) summoned via mouse Middle-Click (`mouse:274`) or Fn key, featuring DeepSeek-Flash reasoning, xAI Grok voice (Sol), screen vision, and 24 native tool modules. Trigger dispatch is ~0.8ms median (`scripts/bench_trigger.py`, mock daemon); end-to-end latency is reported by `KUREK_TIMING=1` logs.

<p align="center">
  <img src="docs/demo.gif" alt="Kureksistant Demo" width="800" />
</p>

---

## ⚡ Quick Install

### Option A: Download Pre-packaged Release (Recommended)

Download the verified `v0.2.0` Linux bundle from the [GitHub Releases](https://github.com/nodaysidle/kureksistant/releases/tag/v0.2.0) page:

```bash
# 1. Download and extract v0.2.0 release archive
curl -LO https://github.com/nodaysidle/kureksistant/releases/download/v0.2.0/kureksistant-v0.2.0-linux-x86_64.tar.gz
tar -xzf kureksistant-v0.2.0-linux-x86_64.tar.gz
cd kureksistant-0.2.0

# 2. Run automated installer (sets up venv, ~/.local/bin/kurek, and desktop icon)
./install_linux.sh

# 3. Add your API keys to .env
cp .env.example .env
nano .env

# 4. Start assistant daemon
kurek start
```

### Option B: Clone from Source

```bash
git clone https://github.com/nodaysidle/kureksistant.git
cd kureksistant
./install_linux.sh
cp .env.example .env   # add DEEPSEEK_API_KEY and XAI_API_KEY
kurek start
curl -s http://127.0.0.1:8790/status
```

Canonical entrypoint: the headless daemon (`kurek_daemon.py`) via `kurek start` / `./launch_kurek.sh`.  
The old PyQt6 JARVIS GUI lives under [`legacy/`](legacy/) and is **unsupported**.

### macOS Native Menu Bar App

```bash
# Start background daemon
./launch_kurek.sh

# Compile and launch native Swift menu bar indicator
swiftc -O -o menubar/KurekBar menubar/KurekBar.swift
./menubar/KurekBar &
```

Set `KUREK_PROJECT_DIR` (or rely on `~/.config/kurek/install_path`) so the menu bar app can locate the daemon if needed.

---

## 🔑 Requirements & Keys (`.env`)

Kureksistant relies on direct high-speed cloud APIs for reasoning and realistic voice synthesis:

| Environment Variable | Required | Description |
|----------------------|----------|-------------|
| `DEEPSEEK_API_KEY`   | **Yes**  | Direct platform API key from `api.deepseek.com` for `deepseek-flash` reasoning. |
| `XAI_API_KEY`        | **Yes**  | xAI Grok Cloud API key from `api.x.ai` for natural speech generation (**Sol** voice). |
| `GEMINI_API_KEY`     | Optional | Google Gemini Flash key for autonomous Wayland monitor vision (`screen_vision`). |
| `DEEPGRAM_API_KEY`   | Optional | Deepgram Nova-2 voice transcription. If omitted, falls back to local `faster-whisper`. |
| `TYPESAFE_API_KEY`   | Optional | TypeSafe Jev System One engine for cognitive snap-judgment triage. |
| `KUREK_USER_NAME`    | Optional | Display name used in the system prompt (default: neutral `"the user"`). |
| `HERMES_PROFILE`     | Optional | Hermes profile name → `~/.hermes/profiles/<name>/memories`. |
| `HERMES_MEMORIES_DIR`| Optional | Absolute/tilde override for Hermes memories (wins over profile). |
| `INPUT_DEVICE`       | Optional | Microphone name substring; empty uses the system default. |
| `KUREK_PROJECT_DIR`  | Optional | Install path for menu-bar daemon auto-spawn (also `~/.config/kurek/install_path`). |
| `KUREK_TIMING`       | Optional | Set to `1` to print per-query ms deltas (trigger receipt, first DeepSeek token/sentence, first `speak_chunk`, mpv `loadfile`). |

**System dependencies:**
- **Linux:** PipeWire with `mpv` (audio playback), `notify-send` (desktop notifications), `grim` (Wayland screen capture), `wl-clipboard` (`wl-copy` / `wl-paste`).
- **macOS:** macOS 14+ with Xcode command line tools (`swiftc`, `afplay`).

---

## ⚠️ Known Limits

- **Arch Linux & Hyprland first:** The mouse shortcut bindings (`mouse:274`) and `grim` screen vision are optimized for Arch Linux under Wayland/Hyprland. Other Wayland environments (Sway, River) require corresponding hotkey binds.
- **Not fully offline:** While audio transcription can fall back to local `faster-whisper`, LLM reasoning (DeepSeek-Flash) and conversational voice output (xAI Grok Sol) require valid cloud API keys and active internet.
- **macOS maturity:** The macOS background daemon and Swift menu bar app (`KurekBar.swift`) are functional, but Wayland-specific tools (`grim` screen capture, `wl-clipboard`) are replaced by standard macOS utilities (`pbcopy`, `screencapture`).
- **Security & Permissions:** Tools execute with local workstation permissions. File creation is unrestricted; file deletion requires mandatory spoken confirmation (*"Yes or No?"*).

---

## ⌨️ Desktop Bindings & CLI

### Hyprland Bindings (`~/.config/hypr/bindings.lua`)
```lua
-- Middle click mouse scroll-wheel: UDS trigger (~0.8ms median, bench_trigger.py)
o.bind("mouse:274", "Summon Kurek Middle Click", "~/.local/bin/kurek-trigger toggle", { mouse = true })
o.bind("SUPER + mouse:274", "Summon Kurek Super+Middle Click", "~/.local/bin/kurek-trigger toggle", { mouse = true })
```

### CLI Commands (`kurek` / `kurek-trigger`)
```bash
kurek toggle           # Toggle microphone listening (~0.8ms median via C trigger)
kurek status           # Check current daemon state & RAM
kurek prompt "..."     # Send text query directly without mic
kurek start            # Launch daemon via systemd user unit
kurek stop             # Stop assistant daemon
kurek-trigger toggle   # Direct C trigger dispatch (~0.8ms median, mock daemon)
```

---

## ⚡ Key Capabilities

- **⚡ Streaming Voice Pipeline:** Sentence-buffered streaming splits response boundaries (with abbreviation/decimal guards). The first complete sentence dispatches to xAI Grok TTS (**Sol** voice) while later tokens still arrive. Enable `KUREK_TIMING=1` for measured TTFB / first-speak / mpv loadfile deltas.
- **🎧 Persistent PipeWire mpv Audio Sink & Instant Barge-in:** Maintains an idle PipeWire `mpv` background process via `$XDG_RUNTIME_DIR/kurek_mpv.sock`. Plays audio chunks straight from `/dev/shm` (POSIX shared RAM tmpfs) with zero NVMe disk writes. Clicking middle-mouse while Kurek is speaking instantly cuts off playback via IPC.
- **🪟 Event-Driven Hyprland Cortex (`.socket2.sock`):** Connects to Hyprland's broadcast event stream and caches focused window class, title, address, and workspace in RAM. `hyprctl` is used once at startup to seed state, then once per vision/screen-capture request for lazy geometry (not on every focus event).
- **👁️ Zero-Disk Window-Targeted Screen Vision:** Crops active window coordinates via `grim -g "<coords>" -t jpeg -q 80 -` piped straight to memory buffers (`io.BytesIO`) into Gemini Flash (no temporary disk writes on the Wayland path).
- **🔌 Unix Domain Socket IPC & C Trigger Client:** Asynchronous socket listener at `$XDG_RUNTIME_DIR/kurek.sock`. Middle-click summon dispatches JSON via a compiled C binary (`bin/kurek-trigger`) — **~0.8ms median** (`scripts/bench_trigger.py`, mock daemon). Real-time state event streaming (`IDLE`, `LISTENING`, `THINKING`, `SPEAKING`) available for Waybar/AGS widgets.
- **📦 Native systemd User Service (`kurek.service`):** Bound to `graphical-session.target` with journald logging, auto-restart on failure, and a resource envelope sized for observed Whisper peak (`MemoryHigh=512M`, `MemoryMax=600M`). The repo unit is a template (`@KUREK_DIR@`); `install_linux.sh` renders it into `~/.config/systemd/user/kurek.service`.
- **🧠 Muse Memory Architecture & TypeSafe Jev:** Three-tier memory plane (Daily Logs `~/memory/`, Durable Core `~/MEMORY.md`, Standing Alignment `~/ALIGNMENT_SYNTHESIS.md`). Cognitive snap-judgment triage powered by TypeSafe Jev auto-promotes durable rules and hoists negative boundaries.
- **⏱️ Process & Build Sentinel (`process_sentinel`):** Monitors long-running compiles, training runs, or test suites (`cargo`, `npm`, `make`, `python`). When the process exits, Kurek dispatches a notification and verbally announces completion time over the speaker via Sol TTS.
- **📋 Voice-to-Clipboard Drafter (`draft_to_clipboard`):** Dictate conventional commits (`feat:`, `fix:`), GitHub PR descriptions, issue reports, or docstrings directly into the Wayland clipboard (`wl-copy`) ready for instant pasting with `Ctrl+V`.
- **📡 Parallel Git Workstation Radar (`workstation_radar`):** Sub-second parallel scan across repositories in `~/Projects` and `~/dev`. Instant voice triage of dirty working trees, untracked files, unpushed commits ahead of upstream, and stashes.
- **🌙 Nightly Dream & Reflection Cycle (`dream_tool`):** Daily subconscious reflection layer that writes an atmospheric prose journal to `~/dreams/YYYY-MM-DD.md` and dynamically distills active behavioral guidance into `~/ALIGNMENT_SYNTHESIS.md`.
- **🔬 Universal Autonomous Research & File Creation:** Deep search across multiple live sources, automated Markdown synthesis, and instant file creation on disk without asking permission. Strict safety confirmation gate required only for file deletion (*"Are you sure you want to delete [file]? Yes or No?"*).
- **🧠 Bidirectional Hermes Memory Continuity:** Optionally synchronizes knowledge with Hermes (`HERMES_PROFILE` or `HERMES_MEMORIES_DIR` in `.env`; default probe `~/.hermes/memories`).
- **🛠️ 24 Native Tool Modules:** Full file management, Playwright browser control, volume/brightness adjusters, Hyprland window tiling, alarms, process watchers, clipboard managers, and application launchers.

---

## 🏗️ Architecture

```mermaid
flowchart TD
    subgraph Inputs ["Summon Triggers"]
        Mouse["🖱️ Middle Click (mouse:274)\nSuper + Middle Click\n(kurek-trigger ~0.8ms median)"]
        Launcher["🚀 Desktop Launcher (kurek.desktop)\nkurek toggle | prompt"]
        MacBar["🍏 macOS Menu Bar (KurekBar.swift)\nFn Global Push-to-Talk"]
    end

    subgraph Daemon ["Kurek Daemon (UDS kurek.sock + HTTP :8790)"]
        State["State Engine (IDLE / LISTENING / THINKING / SPEAKING)"]
        Audio["sounddevice • Adaptive RMS Gate • 1.2s Silence Auto-Submit"]
        STT["STT Engine: Deepgram Nova-2 (Fallback: faster-whisper)"]
        LLM["Brain: DeepSeek-Flash (stream_deepseek_sentences)"]
        TTS["Speech: xAI Grok Cloud Sol (Sentence-Buffered Streaming)"]
        Sink["Audio Sink: Persistent mpv PipeWire Sink (kurek_mpv.sock)\nRAM /dev/shm tmpfs • Instant Barge-in"]
        HyprWatcher["Hyprland Watcher: .socket2.sock (RAM Context)"]
    end

    subgraph Cognition ["Cognitive Plane & Memory"]
        Jev["⚡ TypeSafe Jev (System One Snap Judgment)\nNoul • Choice • Score • Boundary Gate"]
        Muse["🧠 Muse Memory Architecture\n• Daily: ~/memory/YYYY-MM-DD.md\n• Core: ~/MEMORY.md\n• Alignment: ~/ALIGNMENT_SYNTHESIS.md\n• Bank: ~/memory/bank/*.md"]
        Hermes["📡 Hermes Profile Continuity (~/.hermes)"]
    end

    subgraph Tools ["Discovered Actions (actions/)"]
        Vision["👁️ screen_vision • grim + Hyprland Context + Gemini"]
        Clip["📋 manage_clipboard • Persistent Pinning & Recall"]
        Monitor["📈 system_monitor • Live Metrics & 5m Moving Averages"]
        Web["🌐 web_search • DuckDuckGo + Live News Engine"]
        Files["📂 file_controller • Unrestricted Create / Confirmed Delete"]
        Desktop["🖥️ desktop_control & settings • Window Tiling & Audio"]
        Browser["🧭 browser_control • Playwright Headless / Headed"]
        Dev["🛠️ dev_agent • Autonomous Project & App Creator"]
    end

    Inputs -->|HTTP / Socket| State
    State --> Audio --> STT --> LLM
    LLM -->|Cognitive Triage & Hoisting| Jev
    Jev --> Muse
    Muse <--> Hermes
    Muse -->|Prompt Injection| LLM
    LLM -->|Tool Calling| Tools
    Tools -->|Context & Results| LLM
    LLM --> TTS
    Watchers -->|Alerts| TTS
```

---

## 📜 The Evolution of Kurek

```
  ┌──────────────────────────────┐       ┌──────────────────────────────┐       ┌──────────────────────────────┐
  │  Phase 1: The Monolith GUI   │       │  Phase 2: The Headless OS    │       │  Phase 3: Cognitive Memory   │
  │ • Heavy PyQt6 interface      │  ───► │ • Lean Python daemon (:8790) │  ───► │ • Muse Memory 3-Tier Split   │
  │ • Heavy RAM GUI footprint    │       │ • ~450MB peak w/ Whisper     │       │ • TypeSafe Jev System One    │
  │ • Slow visual cold-start     │       │ • Middle Click mouse summon  │       │ • Hourly auto-consolidation  │
  │ • Flat history array         │       │ • 24 Linux tool modules      │       │ • Instant boundary hoisting  │
  └──────────────────────────────┘       └──────────────────────────────┘       └──────────────────────────────┘
```

1. **The Monolith GUI Era (PyQt6 Desktop Client):** In its earliest iteration, Kurek lived as a traditional desktop window app written in PyQt6. While functional, it consumed a large resident RAM footprint, required window switching, and lost context on restart.
2. **The Headless OS Daemon Revolution:** We dismantled the GUI window entirely and rebuilt Kurek as a dedicated Unix-style headless daemon (UDS + `127.0.0.1:8790`). With faster-whisper loaded, observed peak RSS is about ~450MB, and invocation is wired to a mouse Middle Click (`mouse:274`) via `kurek-trigger` (~0.8ms median on a mock daemon).
3. **The Cognitive Leap (Muse Memory & TypeSafe Jev):** Integrated three-tier memory separation and TypeSafe Jev cognitive snap-judgment gating. Casual chat is discarded while durable personal preferences and negative boundaries are automatically hoisted into permanent storage.

---

## 📄 License

[CC BY-NC 4.0](LICENSE) (`SPDX-License-Identifier: CC-BY-NC-4.0`) — derived from FatihMakes' JARVIS ("MARK 53 — JARVIS"). Kureksistant changes by [NODAYSIDLE](https://github.com/nodaysidle). See [NOTICE](NOTICE).

Security reports (including key leaks): see [SECURITY.md](SECURITY.md).
