<p align="center">
  <img src="desktop/kurek.png" alt="Kureksistant Icon" width="128" style="border-radius: 24px;" />
</p>

<p align="center">
  <strong>Kureksistant (Kurek)</strong>
</p>

<p align="center">
  <strong>Autonomous, sub-second personal AI assistant with native screen vision, xAI Grok voice, Wayland clipboard manager, TypeSafe Jev cognition, and Muse Memory. Built for Arch Linux (Hyprland / Omarchy) and macOS. ~55MB RAM.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Arch%20Linux%20(Hyprland)%20%7C%20macOS-1793D1?style=flat-square&logo=arch-linux&logoColor=white" alt="Platform">
  <img src="https://img.shields.io/badge/Brain-DeepSeek--Flash-4E6EF2?style=flat-square&logo=deepseek&logoColor=white" alt="DeepSeek">
  <img src="https://img.shields.io/badge/Cognition-TypeSafe%20Jev-FF5722?style=flat-square" alt="TypeSafe Jev">
  <img src="https://img.shields.io/badge/Voice-xAI%20Grok%20(Sol)-1E1E1E?style=flat-square&logo=x&logoColor=white" alt="xAI Grok">
  <img src="https://img.shields.io/badge/Vision-Gemini%203.5%20Flash-4285F4?style=flat-square&logo=google&logoColor=white" alt="Gemini Vision">
  <img src="https://img.shields.io/badge/Memory-Muse%20Architecture-9C27B0?style=flat-square" alt="Muse Memory">
  <img src="https://img.shields.io/badge/Footprint-~55MB%20RAM-brightgreen?style=flat-square" alt="Memory">
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License">
</p>

---

Kurek (Kurekizmo) is an ultra-fast, local-first personal AI assistant engineered for high-performance developer workstations. It discards bloated 500MB+ Electron containers in favor of a lean ~55MB Python daemon, instant mouse scroll-wheel summoning, native monitor perception via Wayland `grim`, persistent clipboard pinning, real-time web research, self-curating **Muse Memory**, and **TypeSafe Jev** cognitive snap-judgment gating.

---

## 📜 The Evolution of Kurek

```
  ┌──────────────────────────────┐       ┌──────────────────────────────┐       ┌──────────────────────────────┐
  │  Phase 1: The Monolith GUI   │       │  Phase 2: The Headless OS    │       │  Phase 3: Cognitive Memory   │
  │ • Heavy PyQt6 interface      │  ───► │ • Lean Python daemon (:8790) │  ───► │ • Muse Memory 3-Tier Split   │
  │ • ~500MB RAM consumption     │       │ • ~55MB RAM footprint        │       │ • TypeSafe Jev System One    │
  │ • Slow visual cold-start     │       │ • Middle Click mouse summon  │       │ • Hourly auto-consolidation  │
  │ • Flat history array         │       │ • 20 Linux control tools     │       │ • Instant boundary hoisting  │
  └──────────────────────────────┘       └──────────────────────────────┘       └──────────────────────────────┘
```

Kurek did not start as a headless system daemon; it was forged through real daily friction on developer machines:

### 1. The Monolith GUI Era (PyQt6 / Desktop Client)
In its earliest iteration, Kurek lived as a traditional desktop window app written in PyQt6. While functional, it suffered from the fundamental disease of modern desktop AI tools: **unjustified bloat**. It held ~500MB of resident RAM, required window switching, and relied on a flat JSON array of conversation history. If the app restarted, context evaporated.

### 2. The Headless OS Daemon Revolution
We dismantled the monolithic window entirely and rebuilt Kurek as a dedicated Unix-style headless daemon running on `127.0.0.1:8790`:
- **RAM slashed by 90%:** Down from 500MB+ to **~55MB idle**, leaving workstation memory free for compilers and Docker containers.
- **Hardware Summoning:** Wired directly to Arch Linux / Hyprland input events — a single Middle Click (`mouse:274`) on the scroll-wheel toggles listening in sub-second time.
- **Speed & Voice:** Integrated Deepgram Nova-2 STT (auto-submitting on 1.2s silence), DeepSeek-Flash reasoning, and xAI Grok TTS (**Sol** voice) streamed via PipeWire `mpv`.
- **System Power:** Equipped with 20 native actions, including monitor vision via `grim`, sustained 300s CPU/RAM leak watchers, Playwright automation, and autonomous app generation via `dev_agent`.

### 3. The Cognitive Leap: Muse Memory & TypeSafe Jev
The greatest limitation of personal AI agents is **catastrophic forgetting and context pollution**. Either the agent forgets who you are, or its prompt accumulates conversational garbage until reasoning degrades.

Kurek solved this by implementing the **Muse Memory Architecture** powered by **TypeSafe Jev**:
- **3-Tier Separation:** Unstructured chat stays in daily logs (`~/memory/YYYY-MM-DD.md`); durable knowledge lives in curated core files (`~/MEMORY.md`); active operational rules reside in standing guidance (`~/ALIGNMENT_SYNTHESIS.md`).
- **Jev System One Gating:** Integrated TypeSafe Jev for ~50ms calibrated snap judgments (`Noul`, `Choice`, `Score`). Casual chit-chat is discarded; durable personal preferences and non-negotiable boundaries (`salience >= 1.5`) are automatically promoted into permanent storage with mathematical confidence scores.
- **Hermes Harmony:** Synchronizes bidirectionally with Hermes (`eldio`), ensuring personal context remains unified across terminal and voice interfaces.

---

## ⚡ Key Capabilities

- **🧠 Muse Memory Architecture & TypeSafe Jev:** Three-tier memory plane (Daily Logs `~/memory/`, Durable Core `~/MEMORY.md`, Standing Alignment `~/ALIGNMENT_SYNTHESIS.md`). Cognitive snap-judgment triage powered by TypeSafe Jev (`Noul`, `Choice`, `Score`) auto-promotes durable rules and hoists negative boundaries instantly with calibrated confidence scores.
- **👁️ Visual Perception Cortex:** Zero-latency monitor perception via `grim` (Wayland/Hyprland) and active window inspection (`hyprctl activewindow`) analyzed through Gemini 3.5 Flash. One-off diagnosis (*"What is causing this compiler error?"*) and continuous background observation (*"Watch my screen till I say so and critique my layout"*).
- **🎙️ Adaptive VAD & Voice Pipeline:** Ambient noise tracking auto-submits on 1.2s silence. Sub-second transcription via Deepgram Nova-2 (or local Whisper), direct reasoning via DeepSeek-Flash with full reasoning-token persistence, and natural conversational speech using xAI Grok Cloud TTS (**Sol** voice) streamed via PipeWire `mpv` (Linux) or `afplay` (macOS).
- **📋 Persistent Wayland Clipboard Manager:** Background daemon captures every system copy (`wl-paste`). Pin snippets with voice labels (*"Pin my clipboard as Database URL"*), recall pinned clips, or restore them back to the OS clipboard (*"Copy my Stripe key to clipboard"*) without touching the mouse.
- **📈 300-Second Sustained Resource Watcher:** Tracks a 5-minute sliding window of CPU and RAM usage. If average load exceeds 85% sustained over 300 seconds, Kurek identifies the top culprit process, dispatches a desktop notification (`notify-send`), and warns you verbally over the speaker.
- **🔬 Universal Autonomous Research & File Creation:** Deep search across multiple live sources, automated Markdown synthesis, and instant file creation on disk without asking permission. Strict safety confirmation gate required only for file deletion (*"Are you sure you want to delete [file]? Yes or No?"*).
- **🧠 Bidirectional Hermes Memory Continuity:** Automatically synchronizes knowledge and preferences with Hermes (`~/.hermes/profiles/eldio/memories/USER.md` and `MEMORY.md`).
- **🛠️ 20 Native System Tools:** Full file management, Playwright browser control, volume/brightness adjusters, Hyprland window tiling, alarms, and application launchers.

---

## 🏗️ Architecture

```mermaid
flowchart TD
    subgraph Inputs ["Summon Triggers"]
        Mouse["🖱️ Middle Click (mouse:274)\nSuper + Middle Click"]
        Launcher["🚀 Desktop Launcher (kurek.desktop)\nkurek toggle | prompt"]
        MacBar["🍏 macOS Menu Bar (KurekBar.swift)\nFn Global Push-to-Talk"]
    end

    subgraph Daemon ["Kurek Daemon :8790 (~55MB RAM)"]
        State["State Engine (IDLE / LISTENING / THINKING / SPEAKING)"]
        Audio["sounddevice • Adaptive RMS Gate • 1.2s Silence Auto-Submit"]
        STT["STT Engine: Deepgram Nova-2 (Fallback: faster-whisper)"]
        LLM["Brain: DeepSeek-Flash (api.deepseek.com)"]
        TTS["Speech: xAI Grok Cloud Sol (PipeWire mpv / afplay)"]
        Watchers["Background Watchers:\n• 300s CPU/RAM Sliding Window\n• Wayland Clipboard Listener\n• Hourly Muse Consolidation"]
    end

    subgraph Cognition ["Cognitive Plane & Memory"]
        Jev["⚡ TypeSafe Jev (System One Snap Judgment)\nNoul • Choice • Score • Boundary Gate"]
        Muse["🧠 Muse Memory Architecture\n• Daily: ~/memory/YYYY-MM-DD.md\n• Core: ~/MEMORY.md\n• Alignment: ~/ALIGNMENT_SYNTHESIS.md\n• Bank: ~/memory/bank/*.md"]
        Hermes["📡 Hermes Profile Continuity (~/.hermes)"]
    end

    subgraph Tools ["20 Discovered Actions (actions/)"]
        Vision["👁️ screen_vision • grim + Hyprland Context + Gemini 3.5"]
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

## ⌨️ Desktop Bindings & CLI

### Hyprland Bindings (`~/.config/hypr/bindings.lua`)
```lua
-- Middle click mouse scroll-wheel to toggle voice listening
o.bind("mouse:274", "Summon Kurek Middle Click", "/home/arch/.local/bin/kurek toggle", { mouse = true })
o.bind("SUPER + mouse:274", "Summon Kurek Super+Middle Click", "/home/arch/.local/bin/kurek toggle", { mouse = true })
```

### CLI Commands (`kurek`)
```bash
kurek toggle           # Toggle microphone listening
kurek status           # Check current daemon state
kurek prompt "..."     # Send text query directly without mic
kurek start            # Launch daemon in background
kurek stop             # Stop all daemon processes
```

---

## 🔧 Environment Configuration (`.env`)

```bash
DEEPSEEK_API_KEY=sk-...    # Direct platform key for api.deepseek.com
XAI_API_KEY=xai-...        # Direct platform key for api.x.ai (Grok TTS)
GEMINI_API_KEY=AIza...     # Google Gemini 3.5 Flash for screen vision
DEEPGRAM_API_KEY=...      # Deepgram Nova-2 STT (falls back to local Whisper)
TYPESAFE_API_KEY=...      # TypeSafe Jev System One cognitive triage & gatekeeper
```

---

## 🚀 Quickstart

### Arch Linux / Omarchy
```bash
git clone git@github.com:nodaysidle/kureksistant.git
cd kureksistant
./install_linux.sh
kurek start
```

### macOS Native Menu Bar
```bash
./launch_kurek.sh
swiftc -O -o menubar/KurekBar menubar/KurekBar.swift
./menubar/KurekBar &
```

---

## 📄 License

MIT © [Alan Pfeifer (NODAYSIDLE)](https://github.com/nodaysidle)
