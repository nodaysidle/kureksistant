# AGENTS.md — Developer & AI Agent Guide for Kurek (Kureksistant)

> **Kurek** is a headless personal AI assistant for **Arch Linux / Omarchy Quattro (Hyprland)** — a headless daemon with no GUI on purpose (~450MB peak RAM with faster-whisper loaded, observed). Features Middle Click mouse summon (C trigger; `scripts/bench_trigger.py` is an informational, machine-dependent mock-daemon benchmark, not end-to-end latency), native desktop launcher, DeepSeek-Flash reasoning, xAI Grok speech (**Sol** voice), Hermes bidirectional memory continuity, and 27 tool modules in `actions/`. Latest release: v0.2.1 (v0.2.2 unreleased).

---

## 1. System Architecture

```
  ┌──────────────────────────────────────────────┐       ┌──────────────────────────────────────────────┐
  │     Arch Linux / Omarchy Quattro             │       │  (legacy) macOS Menu Bar — unsupported       │
  │  • Middle Click (mouse:274) / Super+Middle   │       │   Swift 6 / AppKit  •  Fn Key Global Monitor │
  │  • Super+Space Launcher (kurek.desktop)      │       │      10 Hz Reactive Polling / State Indicator│
  │  • CLI: kurek [toggle|prompt|status|stop]    │       │                                              │
  └──────────────────────┬───────────────────────┘       └──────────────────────┬───────────────────────┘
                         │                                                      │
                         └───────────────────────┬──────────────────────────────┘
                                                 │ HTTP (127.0.0.1:8790)
                                                 ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        KUREK PYTHON DAEMON (kurek_daemon.py)                           │
│                                                                                        │
│  ┌──────────────────────┐   ┌───────────────────────┐   ┌───────────────────────────┐  │
│  │   Audio Input (STT)  │   │      Brain (LLM)      │   │     Speech Output (TTS)   │  │
│  │  • Deepgram Nova-2   │   │  • DeepSeek-Flash     │   │  • xAI Grok TTS (Sol/sal) │  │
│  │  • faster-whisper    │   │    (api.deepseek.com) │   │    (api.x.ai/v1/tts)      │  │
│  │    (base.en local)   │   │  • Reasoning tokens   │   │  • Linux mpv (PipeWire)   │  │
│  │  • DC offset removal │   │  • 27 tool modules    │   │  • macOS afplay           │  │
│  │  • Peak normalization│   │  • Gated shell opt-in │   │  • In-place notifications │  │
│  └──────────────────────┘   └───────────┬───────────┘   └───────────────────────────┘  │
│                                         │                                              │
│                                         ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │                         24 Computer Control & Core Tools                         │  │
│  │  • open_app           • browser_control     • desktop_control   • reminder       │  │
│  │  • computer_control   • computer_settings   • manage_memory     • weather        │  │
│  │  • file_controller    • dev_agent           • web_search        • youtube_video  │  │
│  └──────────────────────────────────────┬───────────────────────────────────────────┘  │
│                                         │                                              │
│                                         ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │                             Persistent Memory System                             │  │
│  │  • Muse Memory: ~/memory/YYYY-MM-DD.md · ~/MEMORY.md · ~/ALIGNMENT_SYNTHESIS.md │  │
│  │  • Hermes Continuity Sync: HERMES_PROFILE / HERMES_MEMORIES_DIR / ~/.hermes/…  │  │
│  │    (reads full USER.md & MEMORY.md, syncs bidirectional remember calls)          │  │
│  │  • Conversation context: memory/kurek_history.json                              │  │
│  └──────────────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Key Components & File Locations

| Component | File Path | Description |
|-----------|-----------|-------------|
| **Python Daemon** | [`kurek_daemon.py`](kurek_daemon.py) | Headless background daemon. Exposes Unix Domain Socket (`$XDG_RUNTIME_DIR/kurek.sock`) and HTTP port `8790`. Orchestrates audio capture, VAD auto-submitting, STT, DeepSeek LLM, tools, and TTS. |
| **C Trigger Client** | [`bin/kurek-trigger`](bin/kurek-trigger) (`bin/kurek-trigger.c`) | Compiled C binary dispatching JSON over UDS (timing: `scripts/bench_trigger.py`, an informational, machine-dependent benchmark against a mock daemon, not end-to-end latency). Auto-spawns daemon if offline. |
| **Linux CLI & App Launcher** | [`bin/kurek`](bin/kurek) (`~/.local/bin/kurek`) | Single binary control: `kurek [toggle|prompt|status|start|stop]`. Delegates to `kurek-trigger` over UDS. |
| **systemd User Service** | [`desktop/kurek.service`](desktop/kurek.service) (`~/.config/systemd/user/kurek.service`) | Template unit (`@KUREK_DIR@`) rendered by the installer; journald logging and memory envelope (`MemoryHigh=512M`, `MemoryMax=600M`). |
| **Hyprland Event Watcher** | [`core/hyprland_watcher.py`](core/hyprland_watcher.py) | Event-driven `.socket2.sock` listener. Parses focus/open/move/workspace into RAM; one `hyprctl` seed at startup + one lazy geometry fetch per vision request. |
| **PipeWire Audio Sink** | [`core/mpv_sink.py`](core/mpv_sink.py) | Persistent background `mpv` process bound to PipeWire via `$XDG_RUNTIME_DIR/kurek_mpv.sock`. Zero-disk tmpfs playback & instant barge-in. |
| **Desktop Entry** | [`desktop/kurek.desktop`](desktop/kurek.desktop) | Standard FreeDesktop `.desktop` entry. Appears in Omarchy launcher (`Super+Space`) under Apps with 1024x1024 retina icon. |
| **Hyprland Bindings** | `~/.config/hypr/bindings.lua` | Middle Click (`mouse:274`) and `SUPER + mouse:274` calling `kurek-trigger toggle`. |
| **macOS Menu Bar UI (legacy, unsupported)** | [`menubar/KurekBar.swift`](menubar/KurekBar.swift) | Legacy Swift 6 status item for macOS; not maintained or tested. Polls `/status` at 10 Hz and monitors global `Fn` modifier flags. |
| **LLM Client** | [`core/llm_client.py`](core/llm_client.py) | Direct `api.deepseek.com` client with sentence-buffered streaming pipeline (`stream_deepseek_sentences`). |
| **TTS Engine** | [`core/tts.py`](core/tts.py) | Implements `XAITTSEngine` (Grok Cloud TTS, voice: **Sol** / `sal`) with chunk streaming into `MpvPipeWireSink`. |
| **STT Engine** | [`core/stt.py`](core/stt.py) | Implements `OpenRouterSTT` (multilingual `openai/whisper-large-v3-turbo`), `DeepgramSTT` (Nova-2), and `WhisperSTT` (`faster-whisper`) with DC offset stripping and peak normalization. |
| **Memory Tool** | [`actions/memory_tool.py`](actions/memory_tool.py) | `manage_memory(action='remember'|'recall')`. Bidirectionally syncs with Hermes `USER.md` & `MEMORY.md`. |
| **External Memory** | `HERMES_PROFILE` / `HERMES_MEMORIES_DIR` / `~/.hermes/memories` | Optional Hermes `USER.md` + `MEMORY.md` continuity. |
| **Knowledge Vault** | [`actions/vault_knowledge.py`](actions/vault_knowledge.py) | `vault_knowledge(action='list_projects'\|'read'\|'search'\|'add_inbox')`. Accesses nodaysidle-knowledge Obsidian vault notes, PRDs, roadmaps, and social media plans. |
| **Codebase Tool** | [`actions/codebase.py`](actions/codebase.py) | `codebase(action='search'\|'tree'\|'read'\|'find'\|'outline'\|'index'\|'status')`. Ground-truth repo-aware code inspection across allowed project roots. |
| **Run Tool** | [`actions/run.py`](actions/run.py) | `run(action='exec'\|'read'\|'list'\|'cancel')`. Sandboxed, auditable runner for read-only inspection and build/test commands. |
| **Decision Client** | [`core/decision_client.py`](core/decision_client.py) | Optional OpenRouter Decision-1 `needs_screen` gate for auto screen-context; TypeSafe Jev remains the fallback. |
| **Android Remote APK** | [`android/`](android/) | Native Android client with voice dictation, live SSE streaming tokens, and Bearer token auth (`/remote/prompt`) over Tailscale (opt-in `KUREK_REMOTE_BIND`). |

---

## 3. Environment & Secrets

Environment variables are loaded from [`.env`](.env):

```bash
KUREK_TIMING=1            # Optional: print per-query ms timing marks to the daemon log
DEEPSEEK_API_KEY=sk-...   # Direct platform key for api.deepseek.com
XAI_API_KEY=xai-...        # Direct platform key for api.x.ai (Grok TTS)
DEEPGRAM_API_KEY=...      # Deepgram Nova-2 STT (falls back to local Whisper if omitted)
OPENROUTER_API_KEY=       # Optional: Decision-1 auto screen-context (prompt text sent to OpenRouter)
TYPESAFE_API_KEY=         # Optional: Jev triage + Decision-1 fallback for needs_screen
KUREK_AUTO_SCREEN=1       # Set 0 to disable auto screen-context detection
# KUREK_DECISION_TIMEOUT=1.5
KUREK_REMOTE_TOKEN=       # openssl rand -hex 32 — required for any non-loopback HTTP bind
KUREK_REMOTE_BIND=        # empty=loopback only; "tailscale" binds 127.0.0.1 + Tailscale IPv4
KUREK_BIND=               # optional explicit bind host; 0.0.0.0 only if intentional
KUREK_REMOTE_ALLOW_EXEC=  # set 1 to allow remote prompts to run exec/shell tools
```

> [!IMPORTANT]
> DeepSeek chat completions must route straight to `https://api.deepseek.com/chat/completions` (never via OpenRouter). Optional `OPENROUTER_API_KEY` is only for Decision-1 auto screen-context detection; when set, each prompt's text is sent to OpenRouter unless `KUREK_AUTO_SCREEN=0`.

---

## 4. State Machine & Visual Indicators

On Linux, real-time feedback is displayed through in-place desktop notifications:

| State | Notification Badge | Trigger / Meaning |
|-------|--------------------|-------------------|
| **IDLE** | `● Idle` | Ready and waiting for user input. |
| **LISTENING** | `🟢 Listening... (Speak now)` | Mic recording active. Auto-submits on 1.2s silence or max 12s. |
| **THINKING** | `🟡 Thinking...` | Transcribing audio, querying DeepSeek-Flash, or executing tools. |
| **SPEAKING** | `🔵 Speaking...` | Streaming speech audio via xAI Grok TTS (**Sol** voice). |

The legacy macOS `menubar/KurekBar.swift` client is unsupported.

---

## 5. Memory System & Hermes Continuity

Kurek uses the **Muse Memory** three-tier markdown memory plane, triaged by TypeSafe Jev. Auto screen-context detection (`KurekEngine._check_auto_screen_context`) tries an optional fast decision model via OpenRouter Decision-1 first (`core/decision_client.py`), then TypeSafe Jev; with neither key—or with `KUREK_AUTO_SCREEN=0`—no screen context is injected.

1. **Daily logs:** `~/memory/YYYY-MM-DD.md`, the day's captured context.
2. **Durable core:** `~/MEMORY.md`, durable preferences and rules promoted by Jev.
3. **Standing alignment:** `~/ALIGNMENT_SYNTHESIS.md`, behavioural guidance distilled by the nightly dream cycle (`dream_tool`).

Supporting stores:
- `memory/kurek_history.json` keeps the rolling conversation context between turns.
- **Hermes continuity:** When configured, `manage_memory(action='remember')` also appends to Hermes `MEMORY.md`, and Kurek reads Hermes `USER.md` / `MEMORY.md` into its system prompt.

---

## 6. Available Tools & Capabilities

The daemon auto-discovers all tools in the [`actions/`](actions/) directory:

- **`open_app`**: Cross-platform launcher (`xdg-open` / binary on Linux, `open -a` on macOS).
- **`browser_control`**: Full Playwright browser automation (navigate, click, type, scrape).
- **`computer_settings`**: Volume (`pactl` / `osascript`), brightness (`brightnessctl`), Wi-Fi, Bluetooth.
- **`desktop_control`**: Window management (minimize, maximize, hide, tile).
- **`computer_control`**: Simulated keystrokes, mouse clicks, hotkeys, screenshots.
- **`reminder`**: Schedules system alarms and notifications.
- **`web_search`**: DuckDuckGo web search and real-time news retrieval.
- **`manage_memory`**: Stores and retrieves personal knowledge, synced with Hermes.
- **`vault_knowledge`**: Reads and searches the `nodaysidle-knowledge` Obsidian vault (projects, PRDs, social media plans, inbox).
- **`codebase`**: Repo-aware search, tree inspection, line-range read, symbol outlines, index snapshots, and status.
- **`run`**: Sandboxed command runner for read-only inspection and safe build/test commands.
- **`weather_report`**, **`youtube_video`**, **`code_helper`**, **`file_controller`**, etc.

---

## 7. Developer Operations & Quick CLI Commands

### Start Daemon
```bash
kurek start
# Or via script:
./launch_kurek.sh
```

### Stop All Instances
```bash
kurek stop
```

### Check Daemon Status
```bash
kurek status
# Or curl:
curl -s http://127.0.0.1:8790/status
```

### Test Prompt Programmatically (Without Mic)
```bash
kurek prompt "Open browser and check latest Rust news"
```

### Toggle Listening via Mouse or Key
- **Middle Click**: Click mouse scroll wheel (`mouse:274`).
- **Super + Middle Click**: Secondary shortcut.
- **App Launcher**: Press `Super + Space`, type `Kurek`, hit `Enter`.

### View Live Daemon Logs
```bash
tail -f /tmp/kurek_daemon.log
```

### Phone remote (Android, over Tailscale)

HTTP defaults to `127.0.0.1:8790`. For the Android app over Tailscale:

1. In `.env` set `KUREK_REMOTE_TOKEN=$(openssl rand -hex 32)` and `KUREK_REMOTE_BIND=tailscale`, then restart the daemon.
2. Build/install the APK from [`android/`](android/) (`./gradlew :app:assembleDebug`, then `adb install -r app/build/outputs/apk/debug/app-debug.apk`).
3. In the app Settings, enter the PC Tailscale IP/MagicDNS name, port `8790`, and the same token.

The app uses GET `/status` and POST `/remote/prompt` (JSON + `Authorization: Bearer …`, SSE). Transport security is Tailscale/WireGuard (no daemon TLS). Remote prompts block `desktop_control` / `code_helper` / `dev_agent` / `run` / `codebase` unless `KUREK_REMOTE_ALLOW_EXEC=1`. Rotate the token if it was ever shared.
