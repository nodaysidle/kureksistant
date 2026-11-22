<div align="center">
  <img src="desktop/kurek.png" width="144" alt="Kureksistant icon">
  <h1>Kureksistant (Kurek)</h1>
  <p><strong>A headless voice assistant daemon for Arch Linux / Omarchy / Hyprland, with an Android remote over Tailscale.</strong></p>
  <p>Middle-click, talk, get an answer out loud. It can act on your desktop. No GUI, on purpose.</p>

  <p>
    <img src="https://img.shields.io/badge/Linux-Arch%20%2F%20Omarchy%20%2F%20Hyprland-1793D1?style=flat-square&logo=arch-linux&logoColor=white" alt="Arch / Omarchy / Hyprland">
    <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10 or newer">
    <img src="https://img.shields.io/badge/daemon-headless%2C%20no%20GUI-151518?style=flat-square" alt="Headless daemon, no GUI">
    <img src="https://img.shields.io/badge/brain-DeepSeek--Flash-4c8c6b?style=flat-square" alt="DeepSeek-Flash">
    <img src="https://img.shields.io/badge/voice-xAI%20Grok%20%E2%80%A2%20Sol-8e8e96?style=flat-square" alt="xAI Grok voice Sol">
    <img src="https://img.shields.io/badge/tools-27%20modules-4c8c6b?style=flat-square" alt="27 tool modules">
    <img src="https://img.shields.io/badge/Android-Kurek%20Remote%200.2.5-3DDC84?style=flat-square&logo=android&logoColor=white" alt="Kurek Remote 0.2.5">
    <img src="https://img.shields.io/badge/remote-Tailscale%2C%20opt--in-151518?style=flat-square" alt="Remote over Tailscale, opt-in">
    <img src="https://img.shields.io/badge/license-CC%20BY--NC%204.0-lightgrey?style=flat-square" alt="License CC BY-NC 4.0">
    <img src="https://img.shields.io/badge/version-0.2.5-8e8e96?style=flat-square" alt="Version 0.2.5">
  </p>
</div>

Kurek is a Python daemon (`kurek_daemon.py`) that runs as a systemd user service on your Linux desktop. You summon it with a mouse middle-click, a hotkey, the `kurek` command or the phone app. It listens, thinks with DeepSeek-Flash, answers with xAI Grok speech (voice **Sol**), and can act on the desktop through 27 tool modules in [`actions/`](actions/): windows, apps, the browser, files, the clipboard, screen vision, memory and more.

Built and tested on Arch Linux with Omarchy / Hyprland. Release notes are in [CHANGELOG.md](CHANGELOG.md).

## The journey

```text
Summon: middle click, SUPER + SHIFT + K, `kurek` CLI, or the phone
  -> listen: microphone with silence detection (a typed prompt skips this)
  -> transcribe: OpenRouter Whisper -> Deepgram Nova-2 -> local faster-whisper
  -> think: DeepSeek-Flash, with your rules, memory and recent chat in the prompt
  -> act: tool modules in actions/ (read-only lookups in one round run in parallel)
  -> speak: xAI Grok voice Sol, one sentence at a time while the answer still streams
```

| Stage | Engine and file | If it fails |
|---|---|---|
| **Summon** | `bin/kurek-trigger` (C) over the Unix socket `$XDG_RUNTIME_DIR/kurek.sock` | `bin/kurek` starts the daemon if it is offline, then falls back to HTTP on `127.0.0.1:8790` |
| **Listen** | `sounddevice` capture with a voice-activity detector that adapts to your room (`kurek_daemon.py`) | A microphone that cannot start shows a notification |
| **Transcribe** | `core/stt.py`: OpenRouter Whisper if `OPENROUTER_API_KEY` is set, else Deepgram Nova-2 if `DEEPGRAM_API_KEY` is set, else local `faster-whisper` | A failed cloud call retries that utterance on local Whisper |
| **Think** | `core/llm_client.py`: `api.deepseek.com` directly, never through OpenRouter | One retry before any output: at once after a dropped connection, after 1 s on HTTP 429 or 5xx |
| **Act** | `core/action_loader.py` runs each tool with a 120 s limit | A timed-out tool is reported as timed out, never as success, and never retried |
| **Speak** | `core/tts.py` into a persistent `mpv` sink (`core/mpv_sink.py`) on PipeWire | xAI, then OpenRouter (same Grok voices), then Gemini (voice Charon), then `spd-say` |

By default only the Listening and Thinking states show a desktop notification (`KUREK_STATE_NOTIFY=minimal`; `all` shows every state, `off` none).

## Ways to reach it

| Way | How |
|---|---|
| **Middle click** | `mouse:274` runs `kurek-trigger toggle`: first click starts listening, second stops and submits. `SUPER + mouse:274` does the same. |
| **Launcher** | `Super + Space`, type `Kurek`, Enter. |
| **Typed box** | `SUPER + SHIFT + K` opens `kurek-ask`, a one-line prompt in a floating terminal, for names the microphone gets wrong. Enter sends, an empty line closes. A typed "yes" or "no" answers a pending confirmation. |
| **CLI** | `kurek [toggle\|start\|stop\|status\|prompt <text>\|logs [-f]\|doctor]` |
| **Phone** | Kurek Remote (Android) sends prompts over Tailscale and plays the reply. See [Phone remote](#phone-remote). |
| **Live voice** | Opt-in, from the phone app only: talk to Kurek in real time. See [Phone remote](#phone-remote). |

## What it can do

| Group | Tools |
|---|---|
| **Desktop** | `open_app`, `computer_settings` (volume, brightness, Wi-Fi, dark mode, window and tab control), `computer_control`, `desktop_control`, `screen_vision` |
| **Files and notes** | `file_controller`, `file_processor`, `draft_to_clipboard`, `manage_clipboard`, `vault_knowledge` |
| **Web and media** | `web_search`, `browser_control` (Playwright), `youtube_video`, `weather_report`, `flight_finder`, `game_updater`, `send_message` |
| **Code and projects** | `code_helper`, `dev_agent`, `codebase` (read-only), `run` (sandboxed runner), `workstation_radar` |
| **System and time** | `system_monitor`, `process_sentinel`, `reminder` |
| **Memory** | `manage_memory`, `dream_tool` |

Windows are controlled with `hyprctl dispatch` (focus, full screen, maximize, snap left or right, display off). "Close this" and "close tab" close the current tab. A bare "close it" does nothing: Kurek asks what you mean. Only an explicit "close window" closes the whole window.

Risky actions, such as deleting a file, wait for a yes or no. While one is pending, a reply that is entirely a yes or no phrase ("yes", "do it", "no", "cancel") answers it; anything longer is handled as a normal prompt. An authenticated phone request may answer a confirmation raised on the PC, by design. Volume, brightness and dark mode changes can be reversed with `undo`. Files saved under a bare name (`notes.md`) go to `~/Downloads/KUREK/NOTES` (`KUREK_NOTES_DIR` overrides).

## Memory

| Store | Who writes it | Notes |
|---|---|---|
| **Rules** `~/.config/kurek/rules.toml` | You, by hand | Injected verbatim into every prompt as `[USER RULES]`. `pin = true` also repeats a rule after each of your messages. |
| **Learned memory** (`ledger` table in `~/.local/share/kurek/kurek_memory.db`) | `manage_memory` only | Append-only: the database refuses updates and deletes. A correction is a new row that supersedes the old one; "forget" adds a retraction. |
| **Proposals** `~/kurek_proposed_rules.md` | The dream cycle and Jev triage | Proposals only. Nothing reads this file into a prompt; you promote one by copying it into `rules.toml`. |
| **Reference facts** `~/MEMORY.md`, Hermes `USER.md` / `MEMORY.md` | You, and Hermes | Read-only for Kurek. |
| **Chat history** `~/.local/state/kurek/kurek_history.json` | The daemon | The last 10 messages go to the model. Context, never fact. |
| **Daily logs** `~/memory/YYYY-MM-DD.md` | The daemon | Searched when you point at the past ("what did I say about the phone app two days ago?"). The answer carries the date, and what recall finds is never saved as memory unless you ask. |

Who wins: rules, then what you say now, then learned memory, then `~/MEMORY.md` and Hermes, then earlier chat and daily logs. If you say something that goes against a lasting fact (your terminal, your city, where files go), Kurek asks whether it changed before acting. "Kurek, fresh start" clears only the recent chat. Why it is built this way, and how rule-following is measured: [docs/ANTI-DRIFT.md](docs/ANTI-DRIFT.md). Names for each store: [GLOSSARY.md](GLOSSARY.md).

## It does not cut you off

New in 0.2.5. Kurek is not supposed to interrupt your answer, and you are not supposed to be cut off while you speak.

| Guarantee | How it works |
|---|---|
| **Recording ends on your click or the hard cap** | Set `KUREK_AUTO_SUBMIT=off` and only your second click (or `KUREK_MAX_RECORD_SECONDS`, default 180 s) ends a recording. The default, `adaptive`, also submits after a pause: 3.0 s plus 0.3 s per second already spoken, up to 2.0 s more. |
| **Speech starts when the microphone is running** | "Speak now" appears only once audio is flowing, so the first syllable is kept. |
| **Replies are not cut by a timer** | Kurek waits as long as speech keeps playing. Only your click, a new prompt, or 3 minutes with no progress at all stops it. |
| **Background messages wait** | Resource alerts, finished-job announcements and screen-watch comments are said after a reply or recording ends, never over it. After 5 minutes without a quiet moment they become a notification. |
| **A cut answer says so** | When DeepSeek ends an answer early (connection dropped, length limit, or out of resources), Kurek adds one sentence saying the answer is incomplete, on the PC and on the phone. |
| **The phone gets every sentence** | `done` is sent only after every sentence's audio has been sent. A phone that freezes for up to 120 s still receives the rest. |
| **Nothing you typed vanishes** | A new prompt waits up to 150 s for a running tool. If it still cannot run, Kurek says so aloud and the notification shows your words. Prompts up to 64 KB arrive whole; longer ones are refused with a message. |

## Phone remote

Kurek Remote is an Android app that talks to the daemon over Tailscale. It is off until you enable it.

1. In `.env`, set `KUREK_REMOTE_TOKEN` to the output of `openssl rand -hex 32` and `KUREK_REMOTE_BIND=tailscale`, then restart the daemon (`systemctl --user restart kurek`).
2. Check the log (`kurek logs`): it shows two listeners, `127.0.0.1:8790` and your Tailscale IPv4. If Tailscale is not up yet, the daemon stays on loopback and binds the Tailscale address when it appears (HTTP and live voice).
3. Install the APK `kurek-remote-v0.2.5-debug.apk` from the [v0.2.5 release](https://github.com/nodaysidle/kureksistant/releases/tag/v0.2.5), or build it from [`android/`](android/) (Android SDK required, Gradle wrapper included):
   ```bash
   cd android && ./gradlew :app:assembleDebug
   adb install -r app/build/outputs/apk/debug/app-debug.apk
   ```
4. In the app settings enter the PC's Tailscale IPv4 or MagicDNS name, port `8790`, and the same token. The status should show **Online**.

| Feature | Behaviour |
|---|---|
| **Prompts** | `POST /remote/prompt` with `Authorization: Bearer <token>`; the reply streams back as Server-Sent Events (`sentence`, `tool`, `done`). |
| **Dictation** | The mic button fills the prompt field as you speak; you press Send. It asks the phone's recogniser for about 6 seconds of silence before stopping (some phones ignore this). |
| **Silent on the PC** | Remote prompts do not speak through the PC speakers. Opt in per request with `"speak_local": true`, or for all with `KUREK_REMOTE_LOCAL_TTS=1`. |
| **Long tools** | The daemon sends an SSE comment every 15 s, so a slow tool does not trip the app's 90 s read timeout. |
| **Live voice** | Set `KUREK_LIVE_VOICE=1`. The app streams your microphone to `ws://<host>:8791/live` (`KUREK_LIVE_PORT`), and the PC bridges it to xAI's speech-to-speech model (voice Sol). The xAI key stays on the PC. Only read-only tools are offered during a call. xAI bills this per minute (about $0.08, per `.env.example`). |

## Security model

Full policy and how to report a problem: [SECURITY.md](SECURITY.md).

| Area | What the daemon does |
|---|---|
| **Bind** | HTTP binds `127.0.0.1:8790` by default. `KUREK_REMOTE_BIND=tailscale` adds only the Tailscale IPv4. `KUREK_BIND` sets an explicit host; use `0.0.0.0` only if you mean it. |
| **Token** | A remote bind needs `KUREK_REMOTE_TOKEN` of 32 or more characters; without one the daemon falls back to loopback only. Remote POSTs need `Authorization: Bearer <token>`, compared in constant time. A remote `GET /status` needs no token (the app uses it for its online check). |
| **Browser requests** | A request with an `Origin` header is rejected on loopback, there is no CORS, and loopback POSTs must be `application/json`. |
| **Body and timeouts** | Request bodies are capped at 64 KB. A request that stops arriving for 30 s is dropped. A phone stream ends only if the phone accepts nothing for 120 s. |
| **Local access** | The loopback API is unauthenticated by design: any local process can call `/toggle` and `/prompt`. |
| **Exec tools** | Remote prompts cannot use `desktop_control`, `code_helper`, `dev_agent`, `run` or `codebase` unless `KUREK_REMOTE_ALLOW_EXEC=1`. |
| **Secret files** | `file_controller` read and `file_processor` refuse `.env*` (not `.env.example`), `api_keys.json`, `*.pem`, `*.key`, `id_*` keys, `.netrc`, `.git/*` and anything in `~/.ssh`, also through a symlink. |
| **Redaction** | Tool lines in the journal and on the phone are passed through the `run` tool's secret patterns and cut. The model still gets the full result. |
| **Clipboard** | Clips a password manager marks as sensitive are never read or saved. |
| **Secrets in releases** | `.env` and `config/api_keys.json` must never be committed or packaged; `scripts/package_release.sh` refuses to build if they are present. |

**`KUREK_REMOTE_ALLOW_EXEC` is unset by default, so the shell is off.** Setting it to `1` (also `true`, `yes`, `on`) does two things: remote prompts may use the exec-capable tools above, and the `run` tool's unrestricted shell turns on, for local and remote prompts alike. That shell runs any command as your user and bypasses the allowlist and root scoping, and the daemon logs a warning when it is enabled. With the phone remote on, anyone holding your token could run commands on your desktop. Enable it only on a machine and tailnet you fully trust, and rotate the token if it was ever shared.

## Install

### Download the bundle

```bash
BASE=https://github.com/nodaysidle/kureksistant/releases/download/v0.2.5
curl -LO "$BASE/kureksistant-v0.2.5-linux-x86_64.tar.gz"
curl -LO "$BASE/SHA256SUMS.txt"
grep ' kureksistant-v0.2.5-linux-x86_64.tar.gz$' SHA256SUMS.txt | sha256sum -c - && \
tar -xzf kureksistant-v0.2.5-linux-x86_64.tar.gz && \
cd kureksistant-0.2.5 && \
./install_linux.sh
```

### Or clone it

```bash
git clone https://github.com/nodaysidle/kureksistant.git
cd kureksistant
./install_linux.sh
```

`install_linux.sh` is safe to re-run and never overwrites `.env`, `config/api_keys.json` or your memory. It:

- creates `.venv` and installs `requirements.txt`, then the Playwright Chromium;
- compiles `bin/kurek-trigger` with `gcc` and installs `kurek`, `kurek-ask` and `kurek-trigger` into `~/.local/bin`;
- installs the icon and the desktop entry (`kurek.desktop`);
- renders the systemd user unit to `${XDG_CONFIG_HOME:-~/.config}/systemd/user/kurek.service`, which loads `.env` through `EnvironmentFile=-<install dir>/.env`, and enables and starts it;
- creates `.env` from `.env.example` only if it does not exist yet.

| Flag | Effect |
|---|---|
| `--no-enable` | Install the unit but do not enable or start it. |
| `--dev` | Also install `requirements-dev.txt` (pytest, ruff, mypy). |
| `--locked` | Install the exact pins from `requirements.lock`. |

System packages: `mpv`, `grim`, `wl-clipboard`, `libnotify`, `gcc` (the installer lists what is missing), plus `hyprctl` (Hyprland) and Tailscale for the phone. Then add your keys to `.env` (below) and run `kurek doctor`.

### Hyprland bindings

```lua
-- ~/.config/hypr/bindings.lua
o.bind("mouse:274", "Summon Kurek Middle Click", "~/.local/bin/kurek-trigger toggle", { mouse = true })
o.bind("SUPER + mouse:274", "Summon Kurek Super+Middle Click", "~/.local/bin/kurek-trigger toggle", { mouse = true })
o.bind("SUPER + SHIFT + K", "Type to Kurek", "uwsm-app -- xdg-terminal-exec --app-id=com.nodaysidle.kurek-ask --title=Kurek -e ~/.local/bin/kurek-ask")
o.window({ class = "^com.nodaysidle.kurek-ask$" }, { float = true, center = true, size = { 640, 90 } })
```

`SUPER + K`, `SUPER + ALT + K` and `SUPER + CTRL + K` are Omarchy's own keybinding menus, which is why the typed box uses `SUPER + SHIFT + K`.

## Configuration

Settings live in `.env`. These are the ones most people touch; [`.env.example`](.env.example) lists the rest.

| Variable | Needed | Purpose |
|---|---|---|
| `DEEPSEEK_API_KEY` | Yes | The brain (DeepSeek-Flash). |
| `XAI_API_KEY` | Yes | Speech (Grok, voice Sol). Without it speech falls through the chain above. |
| `GEMINI_API_KEY` | Recommended | Screen vision and the Gemini-backed tools. `kurek doctor` reports it missing as a failure. |
| `OPENROUTER_API_KEY` | Optional | Primary speech-to-text (your voice audio is sent to OpenRouter) and typed decisions (prompt text is sent). |
| `DEEPGRAM_API_KEY` | Optional | Deepgram Nova-2 speech-to-text (voice audio is sent to Deepgram). |
| `KUREK_STT_PROVIDER`, `KUREK_STT_LANGUAGE`, `KUREK_WHISPER_MODEL` | Optional | Preferred cloud engine (`openrouter` default, or `deepgram`), a pinned language, and the local model (`base.en` default; use `base` or `small` for other languages). |
| `KUREK_SILENCE_DURATION`, `KUREK_SILENCE_GROWTH`, `KUREK_SILENCE_MAX_EXTRA` | Optional | The pause rule: 3.0 s, 0.3 s per second spoken, up to 2.0 s more. |
| `KUREK_AUTO_SUBMIT`, `KUREK_INITIAL_SILENCE_TIMEOUT`, `KUREK_MAX_RECORD_SECONDS` | Optional | `off` for click-only; give up after 30 s with no speech; 180 s hard cap. |
| `KUREK_NOTES_DIR` | Optional | Where a bare file name is saved (default `~/Downloads/KUREK/NOTES`). |
| `KUREK_USER_NAME`, `INPUT_DEVICE` | Optional | How Kurek addresses you; a microphone name substring. |
| `KUREK_AUTO_SCREEN` | Optional | `1` adds a screen-context decision before every answer (off by default). |
| `KUREK_REMOTE_TOKEN`, `KUREK_REMOTE_BIND` | For the phone | A 32+ character token and `tailscale`. |
| `KUREK_REMOTE_ALLOW_EXEC`, `KUREK_REMOTE_LOCAL_TTS`, `KUREK_LIVE_VOICE` | Optional | See [Security model](#security-model) and [Phone remote](#phone-remote). |

Other things `.env.example` covers: the dictionary (`KUREK_VOCAB_FILE`, corrections read from reverseduo's `dictionary.json`), the claim gate (`KUREK_CLAIM_GATE`), project roots (`KUREK_CODEBASE_ROOTS`), Hermes memory (`HERMES_PROFILE`, `HERMES_MEMORIES_DIR`), memory size (`KUREK_MEMORY_FILE_MAX_CHARS`) and per-turn timing marks (`KUREK_TIMING=1`).

## Where things live

| What | Where |
|---|---|
| API keys and settings | `.env` in the install directory (loaded by the service) |
| Your rules | `~/.config/kurek/rules.toml` (`KUREK_RULES_FILE` overrides) |
| Learned memory | `~/.local/share/kurek/kurek_memory.db` (`$XDG_DATA_HOME/kurek/`) |
| Clipboard history | `~/.local/share/kurek/clipboard_history.json` |
| Claim-gate log | `~/.local/share/kurek/claim-violations.jsonl` |
| Chat history | `~/.local/state/kurek/kurek_history.json` (`$XDG_STATE_HOME/kurek/`) |
| Daemon log | journald (`kurek logs`), else `~/.local/state/kurek/daemon.log` |
| Daily logs | `~/memory/YYYY-MM-DD.md` |
| Proposed rules | `~/kurek_proposed_rules.md` |
| Notes saved by bare name | `~/Downloads/KUREK/NOTES` |
| Control socket | `$XDG_RUNTIME_DIR/kurek.sock` |
| HTTP | `127.0.0.1:8790`; live voice `8791` when enabled |

## When something goes wrong

```bash
kurek doctor      # daemon, socket, service, .env keys (presence only), binaries, microphone, Python
kurek logs -f     # journald when kurek.service is active, else the daemon.log file
kurek status      # daemon state
```

| Symptom | Likely cause |
|---|---|
| No answer at all | `DEEPSEEK_API_KEY` is missing or the network is down. The brain needs the internet; only speech-to-text has a local fallback. |
| Voice sounds robotic or is silent | xAI failed (no key, out of credits) and the chain fell through to `spd-say`, which needs a synthesizer installed. The Gemini free tier allows only a few sentences a minute, so a long reply can lose sentences. |
| Daemon restarted by itself | The service runs with `MemoryHigh=512M` and `MemoryMax=600M`. Past the maximum systemd restarts it and an in-flight request is dropped. |
| "TIMED OUT" from a tool | The tool took over 120 s. Python cannot kill it, so it may finish in the background; Kurek does not retry it. |
| Phone shows Offline or Busy | Offline: wrong host, port or token, or Tailscale is down. Busy: the daemon is still finishing the previous request. |
| A wrong token | Returns `401` (check from another Tailscale device). |
| A thing was claimed done but was not | The claim gate logs it to `claim-violations.jsonl`. It only observes and never changes speech. |

Known limits: Hyprland and Omarchy come first, other Wayland compositors need their own keybinds and the `hyprctl` window actions will not apply. Tools run with your user's permissions; creating files is unrestricted and deleting needs confirmation.

## Stack

| Layer | Technology |
|---|---|
| Daemon | Python 3.10+, `kurek_daemon.py`, HTTP on 8790 and a Unix socket |
| Summon | `bin/kurek-trigger` (C), `bin/kurek` and `bin/kurek-ask` (bash) |
| Brain | DeepSeek-Flash through `api.deepseek.com`; typed decisions on OpenRouter (`typesafe/jev-1.14`, then `microsoft/microsoft-decision-1`) |
| Speech in | OpenRouter Whisper, Deepgram Nova-2, local `faster-whisper` |
| Speech out | xAI Grok TTS, OpenRouter, Gemini, `spd-say`; `mpv` on PipeWire |
| Desktop | `hyprctl`, the Hyprland `.socket2.sock` event stream, `grim`, `wl-clipboard`, `notify-send` |
| Memory | SQLite ledger, TOML rules, markdown daily logs, FTS5 recall index |
| Phone | Native Android app in `android/`, SSE and a WebSocket for live voice |
| Checks | `ruff`, `pytest`, `scripts/verify.sh` (isolated, never touches the live database or port) |

## License

[CC BY-NC 4.0](LICENSE) (`SPDX-License-Identifier: CC-BY-NC-4.0`), derived from FatihMakes' JARVIS ("MARK 53 — JARVIS"). Kureksistant changes by [NODAYSIDLE](https://github.com/nodaysidle). See [NOTICE](NOTICE). Security reports, including key leaks: [SECURITY.md](SECURITY.md).
