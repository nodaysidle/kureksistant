# AGENTS.md — Developer & AI Agent Guide for Kurek (Kureksistant)

> **Kurek** is a headless personal AI assistant for **Arch Linux / Omarchy Quattro (Hyprland)** — a headless daemon with no GUI on purpose (~450MB peak RAM with faster-whisper loaded, observed). Features Middle Click mouse summon (C trigger; `scripts/bench_trigger.py` is an informational, machine-dependent mock-daemon benchmark, not end-to-end latency), native desktop launcher, DeepSeek-Flash reasoning, xAI Grok speech (**Sol** voice), Hermes bidirectional memory continuity, and 27 tool modules in `actions/`. Latest release: v0.2.1 (v0.2.2 unreleased).

---

## 1. System Architecture

```
  ┌──────────────────────────────────────────────┐       ┌──────────────────────────────────────────────┐
  │     Arch Linux / Omarchy Quattro             │       │  Android remote app (over Tailscale)         │
  │  • Middle Click (mouse:274) / Super+Middle   │       │   POST /remote/prompt (Bearer token, SSE)    │
  │  • Super+Space Launcher (kurek.desktop)      │       │   optional per-sentence TTS audio            │
  │  • CLI: kurek [toggle|prompt|status|doctor]  │       │                                              │
  └──────────────────────┬───────────────────────┘       └──────────────────────┬───────────────────────┘
                         │ UDS ($XDG_RUNTIME_DIR/kurek.sock)                    │ HTTP :8790
                         └───────────────────────┬──────────────────────────────┘
                                                 │
                                                 ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        KUREK PYTHON DAEMON (kurek_daemon.py)                           │
│                                                                                        │
│  ┌──────────────────────┐   ┌───────────────────────┐   ┌───────────────────────────┐  │
│  │   Audio Input (STT)  │   │      Brain (LLM)      │   │     Speech Output (TTS)   │  │
│  │  • OpenRouter Whisper│   │  • DeepSeek-Flash     │   │  • xAI Grok TTS (Sol/sal) │  │
│  │  • Deepgram Nova-2   │   │    (V4.1-Flash)       │   │  • sentence queue worker  │  │
│  │  • faster-whisper    │   │  • Reasoning tokens   │   │  • mpv sink (PipeWire)    │  │
│  │    (local fallback)  │   │  • 27 tool modules    │   │  • spd-say fallback       │  │
│  │  • Adaptive VAD      │   │  • Gated shell opt-in │   │  • In-place notifications │  │
│  └──────────────────────┘   └───────────┬───────────┘   └───────────────────────────┘  │
│                                         │                                              │
│                                         ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │                         27 Computer Control & Core Tools                         │  │
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
| **Linux CLI & App Launcher** | [`bin/kurek`](bin/kurek) (`~/.local/bin/kurek`) | Single binary control: `kurek [toggle|prompt|status|start|stop|logs|doctor]`. Delegates to `kurek-trigger` over UDS. `kurek doctor` checks keys (presence only), binaries, mic, socket and service. |
| **systemd User Service** | [`desktop/kurek.service`](desktop/kurek.service) (`~/.config/systemd/user/kurek.service`) | Template unit (`@KUREK_DIR@`) rendered by the installer; journald logging and memory envelope (`MemoryHigh=512M`, `MemoryMax=600M`). |
| **Hyprland Event Watcher** | [`core/hyprland_watcher.py`](core/hyprland_watcher.py) | Event-driven `.socket2.sock` listener. Parses focus/open/move/workspace into RAM; one `hyprctl` seed at startup + one lazy geometry fetch per vision request. |
| **PipeWire Audio Sink** | [`core/mpv_sink.py`](core/mpv_sink.py) | Persistent background `mpv` process bound to PipeWire via `$XDG_RUNTIME_DIR/kurek_mpv.sock`. Zero-disk tmpfs playback & instant barge-in. |
| **Desktop Entry** | [`desktop/kurek.desktop`](desktop/kurek.desktop) | Standard FreeDesktop `.desktop` entry. Appears in Omarchy launcher (`Super+Space`) under Apps with 1024x1024 retina icon. |
| **Hyprland Bindings** | `~/.config/hypr/bindings.lua` | Middle Click (`mouse:274`) and `SUPER + mouse:274` calling `kurek-trigger toggle`. |
| **LLM Client** | [`core/llm_client.py`](core/llm_client.py) | Direct `api.deepseek.com` client with sentence-buffered streaming pipeline (`stream_deepseek_sentences`). |
| **TTS Engine** | [`core/tts.py`](core/tts.py) | Implements `XAITTSEngine` (Grok Cloud TTS, voice: **Sol** / `sal`) with chunk streaming into `MpvPipeWireSink`. |
| **STT Engine** | [`core/stt.py`](core/stt.py) | `OpenRouterSTT` (multilingual `openai/whisper-large-v3-turbo`), `DeepgramSTT` (Nova-2), `WhisperSTT` (local `faster-whisper`, model via `KUREK_WHISPER_MODEL`). Order: OpenRouter → Deepgram → local. Cloud failures raise `STTError`; `FallbackSTT` retries the utterance on local Whisper. |
| **Memory Tool** | [`actions/memory_tool.py`](actions/memory_tool.py) | `manage_memory(action='remember'|'recall')`. Bidirectionally syncs with Hermes `USER.md` & `MEMORY.md`. |
| **External Memory** | `HERMES_PROFILE` / `HERMES_MEMORIES_DIR` / `~/.hermes/memories` | Optional Hermes `USER.md` + `MEMORY.md` continuity. |
| **Knowledge Vault** | [`actions/vault_knowledge.py`](actions/vault_knowledge.py) | `vault_knowledge(action='list_projects'\|'read'\|'search'\|'add_inbox')`. Accesses nodaysidle-knowledge Obsidian vault notes, PRDs, roadmaps, and social media plans. |
| **Codebase Tool** | [`actions/codebase.py`](actions/codebase.py) | `codebase(action='search'\|'tree'\|'read'\|'find'\|'outline'\|'index'\|'status')`. Ground-truth repo-aware code inspection across allowed project roots. |
| **Run Tool** | [`actions/run.py`](actions/run.py) | `run(action='exec'\|'read'\|'list'\|'cancel')`. Sandboxed, auditable runner for read-only inspection and build/test commands. |
| **Decision Client** | [`core/decision_client.py`](core/decision_client.py) | Typed decisions (`needs_screen`, coding intent, ambiguous dev-agent errors) on OpenRouter Decisions: `typesafe/jev-1.14` first, `microsoft/microsoft-decision-1` fallback, then the local heuristic. Answers are validated in Python. |
| **Android Remote APK** | [`android/`](android/) | Native Android client with voice dictation, live SSE streaming tokens, and Bearer token auth (`/remote/prompt`) over Tailscale (opt-in `KUREK_REMOTE_BIND`). |

---

## 3. Environment & Secrets

Environment variables are loaded from [`.env`](.env):

```bash
KUREK_TIMING=1            # Optional: print per-query ms timing marks to the daemon log
DEEPSEEK_API_KEY=sk-...   # Direct platform key for api.deepseek.com
XAI_API_KEY=xai-...        # Direct platform key for api.x.ai (Grok TTS)
DEEPGRAM_API_KEY=...      # Deepgram Nova-2 STT (used when no OpenRouter key; voice audio sent to Deepgram)
OPENROUTER_API_KEY=       # Optional: primary STT (voice audio sent) + Jev/Decision-1 typed decisions (prompt text sent)
GEMINI_API_KEY=           # Gemini-backed tools and screen vision
TYPESAFE_API_KEY=         # Optional: Jev memory triage (direct SDK)
KUREK_AUTO_SCREEN=1       # Set 0 to disable auto screen-context detection
# KUREK_DECISION_TIMEOUT=1.5
KUREK_REMOTE_TOKEN=       # openssl rand -hex 32 — required for any non-loopback HTTP bind
KUREK_REMOTE_BIND=        # empty=loopback only; "tailscale" binds 127.0.0.1 + Tailscale IPv4
KUREK_BIND=               # optional explicit bind host; 0.0.0.0 only if intentional
KUREK_REMOTE_ALLOW_EXEC=  # set 1 to allow remote prompts to run exec/shell tools
```

> [!IMPORTANT]
> DeepSeek chat completions must route straight to `https://api.deepseek.com/chat/completions` (never via OpenRouter). Optional `OPENROUTER_API_KEY` is used for speech-to-text (your voice audio is sent to OpenRouter; local faster-whisper is the fallback) and for typed decisions via OpenRouter Decisions, Jev first then Decision-1 (each prompt's text is sent for screen context unless `KUREK_AUTO_SCREEN=0`). The model name `deepseek-flash` is DeepSeek's unversioned alias, served by DeepSeek-V4.1-Flash-1017 since 2026-10-17. See `.env.example` for every setting.

---

## 4. State Machine & Visual Indicators

On Linux, real-time feedback is displayed through in-place desktop notifications:

| State | Notification Badge | Trigger / Meaning |
|-------|--------------------|-------------------|
| **IDLE** | `● Idle` | Ready and waiting for user input. |
| **LISTENING** | `🟢 Listening... (Speak now)` | Mic recording active. Auto-submits after 2.2s of silence following speech, stops after 6s if no speech starts, hard cap 30s (`KUREK_SILENCE_DURATION`, `KUREK_INITIAL_SILENCE_TIMEOUT`, `KUREK_MAX_RECORD_SECONDS`). |
| **THINKING** | `🟡 Thinking...` | Transcribing audio, querying DeepSeek-Flash, or executing tools. |
| **SPEAKING** | `🔵 Speaking...` | Streaming speech audio via xAI Grok TTS (**Sol** voice). |

By default only Listening and Thinking show a toast (`KUREK_STATE_NOTIFY=minimal`); set `all` for every state or `off` for none.

---

## 5. Memory System & Hermes Continuity

Kurek uses the **Muse Memory** three-tier markdown memory plane, triaged by TypeSafe Jev. Auto screen-context detection (`KurekEngine._check_auto_screen_context`) asks `typesafe/jev-1.14` on OpenRouter Decisions first (`core/decision_client.py`), then `microsoft/microsoft-decision-1`; with no `OPENROUTER_API_KEY`—or with `KUREK_AUTO_SCREEN=0`—no screen context is injected.

1. **Daily logs:** `~/memory/YYYY-MM-DD.md`, the day's captured context.
2. **Reference facts:** `~/MEMORY.md`, hand-written identity, environment and projects. Kurek never appends to it.
3. **Legacy alignment:** `~/ALIGNMENT_SYNTHESIS.md`, still read if present, never written by Kurek.

Rules and learned memory live outside those files:
- **User rules:** `~/.config/kurek/rules.toml`, verbatim, injected as `[USER RULES]`; only the user edits it (`core/rules.py`).
- **Learned memory:** the append-only `ledger` table in `memory/kurek_memory.db`, written by `manage_memory` and Jev (`memory/ledger.py`).
- **Proposals:** `~/kurek_proposed_rules.md`, where the dream cycle and Jev boundary triage only propose rules (`core/proposals.py`). Nothing reads it into a prompt.

**Before changing anything that reads or writes memory, rules or the prompt, read [docs/ANTI-DRIFT.md](docs/ANTI-DRIFT.md)** and run `scripts/rules_eval.py` before and after.

Supporting stores:
- `memory/kurek_history.json` keeps the rolling conversation context between turns.
- **Hermes continuity:** When configured, `manage_memory(action='remember')` also appends to Hermes `MEMORY.md`, and Kurek reads Hermes `USER.md` / `MEMORY.md` into its system prompt.

---

## 6. Available Tools & Capabilities

The daemon auto-discovers all tools in the [`actions/`](actions/) directory:

- **`open_app`**: Launcher (`.desktop` entries / binary / `xdg-open`, focuses via `hyprctl`).
- **`browser_control`**: Full Playwright browser automation (navigate, click, type, scrape).
- **`computer_settings`**: Volume (`pactl` / `osascript`), brightness (`brightnessctl`), Wi-Fi, Bluetooth.
- **`desktop_control`**: Window management (minimize, maximize, hide, tile).
- **`computer_control`**: Simulated keystrokes, mouse clicks, hotkeys, screenshots.
- **`reminder`**: Schedules system alarms and notifications.
- **`web_search`**: DeepSeek Search API ($0.75 per 1000 successful searches) with DuckDuckGo / Google News RSS fallback.
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

### Diagnose Setup
```bash
kurek doctor   # keys (presence only), mpv/wtype/playerctl/spd-say/…, mic, socket, service
```

### View Live Daemon Logs
```bash
kurek logs -f
# journald when kurek.service is active, else ~/.local/state/kurek/daemon.log
```

### Phone remote (Android, over Tailscale)

HTTP defaults to `127.0.0.1:8790`. For the Android app over Tailscale:

1. In `.env` set `KUREK_REMOTE_TOKEN=$(openssl rand -hex 32)` and `KUREK_REMOTE_BIND=tailscale`, then restart the daemon.
2. Build/install the APK from [`android/`](android/) (`./gradlew :app:assembleDebug`, then `adb install -r app/build/outputs/apk/debug/app-debug.apk`).
3. In the app Settings, enter the PC Tailscale IP/MagicDNS name, port `8790`, and the same token.

The app uses GET `/status` and POST `/remote/prompt` (JSON + `Authorization: Bearer …`, SSE). Transport security is Tailscale/WireGuard (no daemon TLS). Remote prompts block `desktop_control` / `code_helper` / `dev_agent` / `run` / `codebase` unless `KUREK_REMOTE_ALLOW_EXEC=1`. Rotate the token if it was ever shared.

<!-- trail:start -->
## Trail — repo context graph

This repo is indexed in `.trail/cache/`: small linked markdown nodes that explain each
system and carry exact file:line spans, kept in sync with the code through git.

For ANY task here — understanding how something works, finding where code lives,
or scoping a change — get context from the graph before grepping or opening
source files. Re-ask freely (it's cheap) and reuse literal identifiers you
already have (symbol, error string, file name) as the query. New to this repo?
Run `trail map` first — a token-budgeted orientation (dir clusters, hubs,
hotspots), no LLM, no key.

- Run `trail ask "<your question>" --source` → ranked nodes with the relevant
  code spans inlined (each hit's ≤8-line crux by default; `--full` for whole
  definitions when the crux isn't enough). Match the tool to the task shape:
  for understanding or editing, the top node IS the answer — cite its
  `covers:` file:line spans and edit straight from `--source`. For
  exhaustive tasks ("every occurrence / every caller of this pattern"), ranked
  results are top-N, not complete — run `trail grep "<literal>"` instead
  (exhaustive over indexed files, grouped by enclosing symbol), falling back
  to raw `grep -rn` only for unindexed files.
- `trail skeleton <file>` → every definition's signature + span, ~10× cheaper
  than reading the file; use it to skim an API surface.
- `trail callers <symbol>` gives precomputed, exact edges — who calls this.
  Add `--direction out` for what it calls, or `--depth N` to walk
  transitively for the full blast radius. For structural questions, skip
  ranking and use this directly.
- Or browse: `.trail/cache/INDEX.md` lists every node; follow the links.
- Monorepos and folders of multiple repos rank fairly across sub-projects —
  hits carry `[scope/]` labels naming which one they're from. Narrow with
  `trail ask "<task>" --in <scope>/` once you know where you're working.

If a returned span is truncated ("+N more lines"), open the file at that exact
range before finalizing. Only open source files when a node genuinely lacks a
needed detail, and then at the exact file:line the node points to — never
re-read whole files.

After big code changes, refresh the graph with `trail build` (deterministic,
no API key, $0).
<!-- trail:end -->

<!-- delegation:v1 -->
## Delegation — Sonnet builds, Haiku swarms, Opus reviews

Launch with `claude --model sonnet --advisor opus`. Sonnet 5.5 runs the session, coordinates, writes the code and applies fixes.

- **Delegate every repo search and test run to the Haiku 5.5 subagents** to keep the main context small:
  - `search`: maps the repo (read-only).
  - `grep`: finds call sites and occurrences in its own context.
  - `audit`: checks the diff and runs the tests in a throwaway worktree. The worktree branches from the default branch, so put the `git diff` output in the audit prompt.
- **Consult the Opus advisor only at three moments:** before a plan locks, when the same error shows up twice, and before calling it done (it signs off on the synthesized diff).
- **Micro-forks** (which file, which tool, retry or stop) need no thinker. Decide them directly, without spawning agents or calling the advisor. When app code here needs routing like this at runtime, use TypeSafe Jev (the `typesafe` skill).
