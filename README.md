<div align="center">
  <img src="desktop/kurek.png" width="128" alt="Kureksistant icon">
  <h1>Kureksistant</h1>
  <p><strong>A headless voice assistant daemon for Arch Linux, Omarchy, and Hyprland, paired with an Android remote over Tailscale.</strong></p>
  <p>Middle-click, talk, get an answer out loud, and let it act on your desktop. No GUI on purpose.</p>

  <p>
    <a href="https://github.com/nodaysidle/kureksistant/releases/tag/v0.2.5"><img src="https://img.shields.io/badge/release-v0.2.5-4c8c6b?style=flat-square" alt="Release v0.2.5"></a>
    <img src="https://img.shields.io/badge/Linux-Arch%20%2F%20Omarchy%20%2F%20Hyprland-1793D1?style=flat-square&logo=arch-linux&logoColor=white" alt="Arch / Omarchy / Hyprland">
    <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/Android-Kurek%20Remote%200.2.5-3DDC84?style=flat-square&logo=android&logoColor=white" alt="Kurek Remote 0.2.5">
    <img src="https://img.shields.io/badge/license-CC%20BY--NC%204.0-lightgrey?style=flat-square" alt="CC BY-NC 4.0">
  </p>
</div>

---

Kurek (`kurek_daemon.py`) runs quietly in the background as a systemd user service. You summon it with a middle-click, a floating one-line prompt, the `kurek` CLI, or your phone. It listens, thinks with DeepSeek-Flash, speaks back sentence by sentence with xAI Grok (voice **Sol**), and acts on your system through 27 tool modules in [`actions/`](actions/).

```text
Middle-click / SUPER+SHIFT+K / CLI / Phone
  -> listen       room-adaptive VAD (or skip straight to text)
  -> transcribe   OpenRouter Whisper -> Deepgram Nova-2 -> local faster-whisper
  -> think        DeepSeek-Flash with your rules, memory ledger, and recent chat
  -> act          27 desktop, file, web, code, and system tools (read-only calls run in parallel)
  -> speak        xAI Grok (Sol) streaming sentence-by-sentence into mpv on PipeWire
```

## Ways to summon it

| Trigger | How it works |
|---|---|
| **Middle-click** | `mouse:274` calls `kurek-trigger toggle` over `$XDG_RUNTIME_DIR/kurek.sock`. First click starts listening, second stops and sends. |
| **Typed box** | `SUPER + SHIFT + K` opens `kurek-ask` in a tiny floating terminal for names the mic gets wrong. Empty line closes it. |
| **Launcher** | `Super + Space`, type `Kurek`, Enter. |
| **CLI** | `kurek [toggle\|start\|stop\|status\|prompt <text>\|logs [-f]\|doctor]` |
| **Phone remote** | **Kurek Remote** (Android) streams prompts over Tailscale, with opt-in real-time live voice. |

## What it can do

- **Hyprland and desktop:** focus apps, toggle fullscreen, maximize, snap windows left/right, turn displays off, adjust volume/brightness/Wi-Fi/dark mode (with `undo`), and inspect the active window with `grim` + Gemini Flash. "Close tab" closes the tab; only an explicit "close window" kills the window.
- **Files, clipboard, and notes:** read, organize, and search files (secret files like `.env`, `*.pem`, and `~/.ssh` are blocked), manage clipboard history without touching password-manager secrets, dictate drafts to `wl-copy`, and query your Obsidian vault.
- **Code and projects:** read-only codebase inspection, git status across `~/dev` (`workstation_radar`), a sandboxed command runner (`run`), and background build/test watchdogs (`process_sentinel`).
- **Web and daily utilities:** search, Playwright browser control, weather, reminders, YouTube, and flights.
- **Confirmations before risky actions:** destructive steps like deleting a file pause for a plain "yes" or "no" (on PC or from an authenticated phone).

## Built not to cut you off

- **Click-only or adaptive listening:** set `KUREK_AUTO_SUBMIT=off` so only your second click ends a recording, or use the default adaptive pause window (3.0 s + 0.3 s per second spoken). "Speak now" only appears once audio is actually flowing.
- **Replies finish playing:** Kurek waits as long as speech is playing instead of cutting off on a fixed timer.
- **Background alerts wait their turn:** system alerts and finished-job announcements wait until a reply or recording ends.
- **Honest failures:** if DeepSeek stops early, Kurek appends a sentence telling you the answer is incomplete. If a tool hits its 120 s limit, it reports a timeout rather than retrying or faking success.

## Memory you control

| Store | Owner | Role |
|---|---|---|
| `~/.config/kurek/rules.toml` | You, by hand | Injected verbatim as `[USER RULES]`. `pin = true` repeats a rule right after your message. No code writes this file. |
| `~/.local/share/kurek/kurek_memory.db` | `manage_memory` | Append-only SQLite ledger. Corrections supersede old rows without deleting history. |
| `~/kurek_proposed_rules.md` | Dream cycle / triage | Suggestions only; never read into prompts until you copy one into `rules.toml`. |
| `~/memory/YYYY-MM-DD.md` | Daemon | Daily logs indexed with FTS5 for past-conversation recall ("what did I say two days ago?"). |

Priority order: your rules, what you say right now, learned memory, reference files (`~/MEMORY.md` / Hermes), then recent chat and daily logs. Say "Kurek, fresh start" to clear only the recent chat. Deep dive: [docs/ANTI-DRIFT.md](docs/ANTI-DRIFT.md) and [docs/GLOSSARY.md](docs/GLOSSARY.md).

## Install

Requires Arch Linux (Omarchy / Hyprland recommended), Python 3.10+, `mpv`, `grim`, `wl-clipboard`, `libnotify`, and `gcc`.

```bash
git clone https://github.com/nodaysidle/kureksistant.git
cd kureksistant
./install_linux.sh
```

Or download the verified release tarball from [Releases](https://github.com/nodaysidle/kureksistant/releases/tag/v0.2.5).

`install_linux.sh` is safe to re-run and never overwrites `.env` or memory. It creates `.venv`, installs dependencies and Playwright Chromium, compiles `kurek-trigger`, installs `kurek` / `kurek-ask` / `kurek-trigger` to `~/.local/bin`, and sets up `kurek.service` (flags: `--no-enable`, `--dev`, `--locked`).

Add your keys to `.env` (minimum: `DEEPSEEK_API_KEY` and `XAI_API_KEY`; `GEMINI_API_KEY` recommended for screen vision), then run:

```bash
kurek doctor
```

### Hyprland bindings

```lua
-- ~/.config/hypr/bindings.lua
o.bind("mouse:274", "Summon Kurek Middle Click", "~/.local/bin/kurek-trigger toggle", { mouse = true })
o.bind("SUPER + mouse:274", "Summon Kurek Super+Middle Click", "~/.local/bin/kurek-trigger toggle", { mouse = true })
o.bind("SUPER + SHIFT + K", "Type to Kurek", "uwsm-app -- xdg-terminal-exec --app-id=com.nodaysidle.kurek-ask --title=Kurek -e ~/.local/bin/kurek-ask")
o.window({ class = "^com.nodaysidle.kurek-ask$" }, { float = true, center = true, size = { 640, 90 } })
```

## Phone remote (Kurek Remote)

Remote access is **off by default** (loopback only on `127.0.0.1:8790`). To pair the Android app over Tailscale:

1. In `.env`, set `KUREK_REMOTE_TOKEN=$(openssl rand -hex 32)` (32+ chars required) and `KUREK_REMOTE_BIND=tailscale`, then run `systemctl --user restart kurek`.
2. Install `kurek-remote-v0.2.5-debug.apk` from [v0.2.5](https://github.com/nodaysidle/kureksistant/releases/tag/v0.2.5), or build from [`android/`](android/) with `./gradlew :app:assembleDebug`.
3. Enter your PC's Tailscale IPv4 or MagicDNS name, port `8790`, and your token in the app settings.

Remote prompts stay silent on the PC by default (`KUREK_REMOTE_LOCAL_TTS=1` opts in) and block exec-capable tools unless `KUREK_REMOTE_ALLOW_EXEC=1`. Set `KUREK_LIVE_VOICE=1` to enable real-time voice calls over `ws://<host>:8791/live` (read-only tools only; billed per minute by xAI).

## Documentation

- [docs/REFERENCE.md](docs/REFERENCE.md): full `.env` configuration, file paths, security model, troubleshooting, and stack details
- [docs/ANTI-DRIFT.md](docs/ANTI-DRIFT.md): memory invariants and rule-following evaluation
- [docs/GLOSSARY.md](docs/GLOSSARY.md): canonical names for every memory store
- [.github/SECURITY.md](.github/SECURITY.md): security policy and vulnerability reporting
- [CHANGELOG.md](CHANGELOG.md): release history

## License

[CC BY-NC 4.0](LICENSE) (`SPDX-License-Identifier: CC-BY-NC-4.0`), derived from FatihMakes' JARVIS ("MARK 53"). Kureksistant changes by [NODAYSIDLE](https://github.com/nodaysidle). See [NOTICE](NOTICE).
