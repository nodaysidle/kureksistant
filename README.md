<p align="center">
  <img src="desktop/kurek.png" alt="Kureksistant icon" width="128" />
</p>

<h1 align="center">Kureksistant (Kurek)</h1>

<p align="center">
  <strong>Autonomous voice assistant daemon for Arch Linux (Omarchy / Hyprland), paired with Kurek Remote Android over Tailscale.</strong>
</p>

<p align="center"><strong>Headless Linux daemon (~333MB RAM), summoned via middle-click. No GUI on purpose.</strong></p>

<p align="center">
  <a href="https://github.com/nodaysidle/kureksistant/releases/tag/v0.2.3"><img src="https://img.shields.io/badge/Release-v0.2.3-blue.svg?style=flat-square" alt="Release v0.2.3"></a>
  <img src="https://img.shields.io/badge/Desktop-Arch%20%2F%20Omarchy%20%2F%20Hyprland-1793D1?style=flat-square&logo=arch-linux&logoColor=white" alt="Arch / Omarchy / Hyprland">
  <img src="https://img.shields.io/badge/Remote-Android%20app%20(APK%20in%20v0.2.3)-3DDC84?style=flat-square&logo=android&logoColor=white" alt="Android remote">
  <img src="https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey?style=flat-square" alt="License">
</p>

---

**A headless voice assistant daemon on Linux — no GUI on purpose.**

Kurek is a headless Python daemon (`kurek_daemon.py`) with no GUI that runs as a systemd user service on your Linux desktop. You summon it with a mouse middle-click, a hotkey or the `kurek` CLI. It listens, thinks with DeepSeek-Flash (DeepSeek-V4.1-Flash), talks back with xAI Grok TTS (voice **Sol**), and can act on your desktop through 27 tool modules in [`actions/`](actions/), including windows, apps, the browser, files, the clipboard, screen vision and memory.

It is built and tested on **Arch Linux with Omarchy / Hyprland**. The old macOS menu-bar client and PyQt6 GUI were removed in v0.2.2.

**Latest release: [v0.2.3, live voice on the phone](https://github.com/nodaysidle/kureksistant/releases/tag/v0.2.3)** (see also [CHANGELOG.md](CHANGELOG.md)).

---

## ✨ Features (v0.2.3)

### Omarchy / Hyprland desktop integration
- **Terminals and shell:** Ghostty is the default terminal and Herdr runs inside it, with a Fish shell alias. btop/htop open in a terminal.
- **Native `hyprctl dispatch` window control:**
  - cycle windows;
  - focus an already-open app (`workspace` + `focuswindow address:`);
  - full-screen toggle, which reads the current state and sets the opposite;
  - maximize (`fullscreen 1`);
  - snap the active window to the left or right half of the focused monitor;
  - display off (`dpms`).
- **Close:** "close this", "close it" and "close tab" close the current tab (Ctrl+W). Only an explicit "close window" closes the whole window (`killactive`).
- **Screen vision:** `grim` captures the active window and sends it to Gemini Flash. Hyprland context comes from the `.socket2.sock` event stream.
- **Summon:** a compiled C trigger (`bin/kurek-trigger`) over the Unix socket `$XDG_RUNTIME_DIR/kurek.sock`.

### Phone remote over Tailscale (opt-in)
- An Android app, **Kurek Remote**, is built from [`android/`](android/). It sends prompts to `POST /remote/prompt` and receives the reply as a Server-Sent Events stream (`sentence` / `tool` / `done`).
- Off by default. Enable it with `KUREK_REMOTE_BIND=tailscale` and a Bearer token of **32+ characters** (`KUREK_REMOTE_TOKEN`).
- **Silent on the PC by default:** remote prompts don't speak through the PC's speakers. Opt in per request with `"speak_local": true`, or globally with `KUREK_REMOTE_LOCAL_TTS=1`.
- **Live voice (opt-in, `KUREK_LIVE_VOICE=1`):** tap Talk live in the app, or the 2x1 home-screen widget, and talk to Kurek in real time. The phone streams your mic to the PC over `ws://<host>:8791/live`, and the PC bridges it to xAI's speech-to-speech model (voice Sol). The xAI key stays on the PC. During a call Kurek can run read-only lookups (system status, weather, web search, repo status, the vault, memory recall). Calls keep running with the screen off, with an ongoing notification and an End button. xAI bills this per minute.
- The APK (Kurek Remote 0.2.3) is attached to the [v0.2.3 release](https://github.com/nodaysidle/kureksistant/releases/tag/v0.2.3). You can also build it yourself (see below).

### Conversational yes/no confirmations
- Risky actions ask for confirmation, shown on the PC via `notify-send`.
- While one is pending, a reply that is **entirely** a yes/no word or phrase confirms or cancels it ("yes", "ok", "do it", "no", "cancel", "never mind" and so on).
- Anything longer, such as "notes for today" or "stop the music", is handled as a normal prompt.
- **By design**, an authenticated phone request may confirm or cancel a confirmation that was raised on the PC.

### Memory: rules you control, and a ledger that can't be rewritten
- **Rules, `~/.config/kurek/rules.toml`:** your standing rules, stored word for word and injected into every prompt as `[USER RULES]`. Only you edit this file; no model writes to it. A missing file means no rules. A malformed one injects a visible "rules unavailable" notice and logs the error.
  ```toml
  [[rule]]
  id = "never-lie"
  text = "Never lie. If you are not sure, always check the web, then answer."
  pin = true   # optional: also re-send this rule, verbatim, right after each of your messages
  ```
- **Learned memory, an append-only ledger:** what Kurek learns through `manage_memory` and Jev triage goes into a `ledger` table in `memory/kurek_memory.db`. The database refuses updates and deletes, and every row needs a source. A correction is a new row that supersedes the old one (the same key does it automatically, or pass `replaces=<#id>`), so history stays and the prompt carries only entries nothing has replaced. The block is capped by `KUREK_MEMORY_FILE_MAX_CHARS`; if the oldest entries have to be left out, a warning is logged.
- **Proposals, `~/kurek_proposed_rules.md`:** the nightly dream cycle and Jev's boundary triage only *propose* rules here, with a source and your words verbatim. Nothing reads this file back into a prompt. You promote a proposal by copying it into `rules.toml`.
- **Everything else:** daily logs in `~/memory/`, a hand-written reference in `~/MEMORY.md`, and `~/ALIGNMENT_SYNTHESIS.md`, which is still read if present but is no longer written by Kurek. Memory files longer than the cap are cut to head and tail, with a warning in the log. Optional Hermes continuity is available via `HERMES_PROFILE` / `HERMES_MEMORIES_DIR`.
- **Recall:** ask about a past conversation ("what did I say about the phone app two days ago?") and Kurek searches the daily logs with several keywords, then answers with the date and what was said. What recall finds is never saved as memory unless you ask.
- **Who wins:** rules, then what you say now, then learned memory, then `~/MEMORY.md` and Hermes, then earlier chat and daily logs. If you say something that goes against a lasting fact (your terminal, city, where files go), Kurek asks whether it changed before acting and saves the change only after your yes. When you correct learned memory outside the chat (by hand or with another agent), chat turns from before the correction stop going to the model, with a warning in the log.
- **Fresh start:** say "Kurek, fresh start" (or "new conversation") to clear only the recent chat in `memory/kurek_history.json`. Rules, learned memory and daily logs stay.
- **Terms:** [GLOSSARY.md](GLOSSARY.md) names each store.
- **Why it is built this way, and how rule-following is measured:** [docs/ANTI-DRIFT.md](docs/ANTI-DRIFT.md).
- **Storage:** learned memory is the `ledger` table in `memory/kurek_memory.db` (SQLite). The legacy `memory/long_term.json` store is retired: it is no longer read or imported, and is left on disk untouched.

### Stability: the 600M systemd memory limit
- `kurek.service` runs with `MemoryHigh=512M` / `MemoryMax=600M` and `Restart=on-failure`.
- Heavy child jobs (dev-agent builds, pip installs, project runs) are started in their own `systemd-run --user --scope`, so their memory isn't counted against the service. Chromium is launched with low-memory flags.
- Tool calls time out after **120 s**. A timed-out tool is reported as **timed out, never as success, and never retried**. Calling the same tool again while the earlier call is still running is refused.

### Environment-first API keys
- Keys are read from the environment / `.env` first.
- For the Gemini key, `config/api_keys.json` is the legacy fallback. A missing key no longer crashes the actions that use it.

### Other tools (unchanged)
- `process_sentinel`: announces when long builds or tests finish.
- `draft_to_clipboard`: dictate commit messages and PR text into `wl-copy`.
- `workstation_radar`: git status across the repos under `~/dev`.
- `vault_knowledge`: queries the nodaysidle-knowledge Obsidian vault. Set `KUREK_VAULT_DIR`, or it looks in `~/dev/nodaysidle/nodaysidle-knowledge`.
- `open_app`: launches desktop apps by name, including the Grok Bot app (`grok-bot`).
- `dream_tool`: nightly reflection journal.
- Also: Playwright browser control, web search, file management (deletion requires confirmation), volume/brightness, alarms, and the clipboard manager.

---

## ⚡ Install (Linux)

### Option: download the v0.2.3 bundle

Download [`kureksistant-v0.2.3-linux-x86_64.tar.gz`](https://github.com/nodaysidle/kureksistant/releases/download/v0.2.3/kureksistant-v0.2.3-linux-x86_64.tar.gz) and [`SHA256SUMS.txt`](https://github.com/nodaysidle/kureksistant/releases/download/v0.2.3/SHA256SUMS.txt), then:

```bash
grep ' kureksistant-v0.2.3-linux-x86_64.tar.gz$' SHA256SUMS.txt | sha256sum -c - && \
tar -xzf kureksistant-v0.2.3-linux-x86_64.tar.gz && \
cd kureksistant-0.2.3 && \
./install_linux.sh
```

### 1. Clone and run the installer

```bash
git clone https://github.com/nodaysidle/kureksistant.git
cd kureksistant
./install_linux.sh
```

`install_linux.sh` does the following:
- creates `.venv` and installs `requirements.txt` plus the Playwright Chromium;
- compiles `kurek-trigger`;
- installs `~/.local/bin/kurek`, the icon and the desktop entry;
- renders the systemd user unit into `${XDG_CONFIG_HOME:-~/.config}/systemd/user/kurek.service`. The unit loads your settings with `EnvironmentFile=-<install dir>/.env`;
- enables and starts `kurek.service` (skip with `--no-enable`);
- creates `.env` from `.env.example` only if it doesn't exist yet.

Flags: `--no-enable`, `--dev` (also installs `requirements-dev.txt`), `--locked` (installs the exact pins in `requirements.lock`). Afterwards run `kurek doctor` to check keys, binaries, mic and service.

### 2. Configure `.env`

```bash
nano .env
```

| Variable | Required | Purpose |
|---|---|---|
| `DEEPSEEK_API_KEY` | **Yes** | Reasoning and tool dispatch (DeepSeek-Flash). |
| `XAI_API_KEY` | **Yes** | Speech (xAI Grok TTS, voice Sol). |
| `GEMINI_API_KEY` | Optional | Screen vision and Gemini-backed tools. |
| `DEEPGRAM_API_KEY` | Optional | Deepgram Nova-2 STT (voice audio is sent to Deepgram). |
| `TYPESAFE_API_KEY` | Optional | TypeSafe Jev memory triage (direct SDK). |
| `OPENROUTER_API_KEY` | Optional | **Primary STT** (your voice audio is sent to OpenRouter Whisper) and typed decisions (Jev, then Decision-1) for auto screen-context and code intent (prompt text is sent). |
| `KUREK_STT_PROVIDER` / `OPENROUTER_STT_MODEL` | Optional | Prefer `openrouter` (default) or `deepgram`; OpenRouter STT model. |
| `KUREK_WHISPER_MODEL` | Optional | Local faster-whisper model (default `base.en`, English-only; `base`/`small` are multilingual). Used with no cloud STT key, and as the fallback when cloud STT fails. |
| `KUREK_SILENCE_DURATION` / `KUREK_INITIAL_SILENCE_TIMEOUT` / `KUREK_MAX_RECORD_SECONDS` | Optional | Listening window: 2.2 s / 6 s / 30 s. |
| `KUREK_STATE_NOTIFY` | Optional | `minimal` (default), `all` or `off` state toasts. |
| `KUREK_MEMORY_FILE_MAX_CHARS` | Optional | Cap per memory file, and for the learned-memory block, injected into each prompt (default 6000). A warning is logged when something is cut. |
| `KUREK_RULES_FILE` | Optional | Path to `rules.toml` (default `~/.config/kurek/rules.toml`). |
| `KUREK_VAULT_DIR` | Optional | Path to the Obsidian vault used by `vault_knowledge`. |
| `KUREK_CODEBASE_ROOTS` | Optional | Project roots for the `codebase` tool (`:`-separated). |
| `KUREK_AUTO_SCREEN` | Optional | `0` disables auto screen-context. |
| `KUREK_DECISION_TIMEOUT` | Optional | Decision request timeout in seconds (Jev and Decision-1). |
| `KUREK_USER_NAME` | Optional | How Kurek addresses you. |
| `HERMES_PROFILE` / `HERMES_MEMORIES_DIR` | Optional | Hermes memory continuity. |
| `INPUT_DEVICE` | Optional | Microphone name substring. |
| `KUREK_TIMING` | Optional | `1` logs per-query timing deltas. |
| `KUREK_REMOTE_BIND` | For phone | `tailscale` enables the remote bind (see below). |
| `KUREK_REMOTE_TOKEN` | For phone | Bearer token, **at least 32 characters** (`openssl rand -hex 32`). |
| `KUREK_REMOTE_ALLOW_EXEC` | Optional | `1` lets remote prompts use exec-capable tools and enables the `run` tool's unrestricted shell. |
| `KUREK_REMOTE_LOCAL_TTS` | Optional | `1` makes remote prompts also speak on the PC. |
| `KUREK_BIND` | Optional | Explicit bind host. Never use `0.0.0.0` unless you understand the risk. |

Phone-remote example:

```bash
# openssl rand -hex 32  → paste the output as the token
KUREK_REMOTE_TOKEN=<64-hex-char token>
KUREK_REMOTE_BIND=tailscale
```

### 3. Start the service

```bash
systemctl --user daemon-reload
systemctl --user enable --now kurek     # or: kurek start
systemctl --user status kurek
journalctl --user -u kurek -f           # the log shows the bind addresses
curl -s http://127.0.0.1:8790/status
```

With `KUREK_REMOTE_BIND=tailscale`, the log shows two listeners: `127.0.0.1:8790` and your Tailscale IPv4 (`100.x.y.z:8790`). If Tailscale isn't up yet at boot, the daemon stays on loopback and retries.

**System dependencies (Linux):**
- PipeWire with `mpv`;
- `notify-send`;
- `grim`;
- `wl-clipboard`;
- `hyprctl` (Hyprland);
- a C compiler for `kurek-trigger`;
- Tailscale for the phone remote.

### Hyprland bindings

```lua
-- ~/.config/hypr/bindings.lua
o.bind("mouse:274", "Summon Kurek Middle Click", "~/.local/bin/kurek-trigger toggle", { mouse = true })
o.bind("SUPER + mouse:274", "Summon Kurek Super+Middle Click", "~/.local/bin/kurek-trigger toggle", { mouse = true })
```

### CLI

```bash
kurek toggle           # start/stop listening
kurek status           # daemon state
kurek prompt "..."     # text prompt without the mic
kurek start | stop     # systemd user service
kurek-trigger toggle   # C trigger over the Unix socket
```

---

## 📱 Android remote (Kurek Remote)

The APK (Kurek Remote 0.2.3) is attached to the [v0.2.3 release](https://github.com/nodaysidle/kureksistant/releases/tag/v0.2.3). **If you installed the v0.2.1 APK, uninstall it once first:** 0.2.2 and later are signed with a different key. From 0.2.2 on, updates install in place. Or build it from [`android/`](android/) (the Android SDK is required; the Gradle wrapper is included):

```bash
cd android
./gradlew :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

In the app's settings, fill in:
- **Host:** the PC's Tailscale IPv4 (`tailscale ip -4`) or MagicDNS name.
- **Port:** `8790`.
- **Token:** the same `KUREK_REMOTE_TOKEN`.

The status should show **ONLINE**.

**Check it from another Tailscale device:**
- A prompt with the correct token returns `200` with `text/event-stream`.
- A wrong or missing token returns **`401`**.

Traffic is plain HTTP inside Tailscale/WireGuard. The daemon doesn't terminate TLS itself.

---

## 🔒 Security model (summary)

Full details and how to report issues: **[SECURITY.md](SECURITY.md)**.

- **Bind:** HTTP binds to `127.0.0.1:8790` by default. Remote access is opt-in with `KUREK_REMOTE_BIND=tailscale`, which adds only the Tailscale IPv4.
- **Token:** if the remote token is missing or shorter than 32 characters, the daemon **refuses** to bind remotely. Non-loopback requests need `Authorization: Bearer <token>`, compared in constant time (`hmac.compare_digest`).
- **Browser requests:** cross-origin/CSRF requests carrying an `Origin` header are rejected, and there is no `CORS *`.
- **Body limit:** request bodies are capped at 64 KB.
- **Exec tools:** remote prompts can't use exec-capable tools (`desktop_control`, `code_helper`, `dev_agent`) unless `KUREK_REMOTE_ALLOW_EXEC=1`.
- **Phone confirmations:** a remote client with the token can confirm pending confirmations, as described above. Treat the token like a password, and rotate it (`openssl rand -hex 32`) if it was ever shared or shipped in a build.
- **Local access:** the loopback API is unauthenticated by design. Any local process can call it.
- **Secrets:** `.env` and `config/api_keys.json` must never be committed or packaged.

---

## ⬆️ Upgrading from v0.2.0

1. Update the code (`git pull`) and re-run `./install_linux.sh`. This re-renders the unit with `EnvironmentFile` and keeps your `.env`.
2. Restart at a quiet time:
   ```bash
   systemctl --user daemon-reload && systemctl --user restart kurek
   ```
3. If you use the phone remote, send a test prompt from the app, and check that a wrong token returns `401`.

---

## ⚠️ Unrestricted shell (`KUREK_REMOTE_ALLOW_EXEC`) — off by default

The `run` tool is a sandboxed, allowlisted, root-scoped command runner by default. An **unrestricted shell mode** exists that runs *any* command as your user, bypassing the allowlist and root scoping. It is **OFF** unless you explicitly set:

```bash
KUREK_REMOTE_ALLOW_EXEC=1
```

The older `KUREK_UNRESTRICTED_SHELL` / `KUREK_ALLOW_SHELL` variables no longer enable it on their own. When enabled, the daemon logs a loud warning and the tool description and system prompt say so. Combined with the phone remote, anyone holding your remote token could run arbitrary commands on your desktop. Only enable it on a machine and tailnet you fully trust.

## ⚠️ Known limits

- **Not offline:** reasoning (DeepSeek-Flash) needs internet. Speech-to-text falls back to local `faster-whisper`, and voice falls back to the robotic `spd-say` when xAI is unreachable.
- **Desktop scope:** Hyprland/Omarchy comes first. Other Wayland compositors need their own keybinds, and the Hyprland-specific window actions won't apply there.
- **Timed-out tools keep running:** they can't be killed. After 120 s Kurek reports the timeout, but the tool may finish in the background.
- **Confirmation wording:** after a "yes", Kurek replies "Confirmed, doing it now." as the action starts; a failure is logged and shown as a notification.
- **Memory headroom:** the service can run close to `MemoryHigh=512M` with faster-whisper and Chromium loaded. If it exceeds `MemoryMax=600M`, systemd restarts it, and an in-flight request is dropped.
- **Local tool permissions:** tools run with your user's permissions. Creating files is unrestricted, and deleting needs confirmation.
- **Lint:** CI enforces a subset of ruff rules (see `[tool.ruff.lint]` in `pyproject.toml`); stricter rules are a ratchet target.

---

## 📄 License

[CC BY-NC 4.0](LICENSE) (`SPDX-License-Identifier: CC-BY-NC-4.0`) — derived from FatihMakes' JARVIS ("MARK 53 — JARVIS"). Kureksistant changes by [NODAYSIDLE](https://github.com/nodaysidle). See [NOTICE](NOTICE).

Security reports (including key leaks): see [SECURITY.md](SECURITY.md).
