# Changelog

All notable changes to Kureksistant (Kurek) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project approximates [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- **Reply length and language live only in `rules.toml`.** The system prompt now defers to `[USER RULES]` for both, with a short-reply, user's-language fallback when no such rule exists. `tests/test_anti_drift_prompts.py` enforces this and checks that nothing but `core/rules.py` touches the rules file.
- **rules_eval:** the `english-only` case is replaced by `slovenian-allowed` and `other-language-gets-english`.

### Removed
- **`core/prompt.txt`:** no code loaded it, and keeping it in step with the real prompt was a source of drift.

### Documentation
- **Correction to the 0.2.0 entry below:** it lists "~0.8ms median trigger, 24 tools" as measured figures. The 0.2.0/0.2.1 tool count was **25** (25 `actions/*.py` files defined a `TOOL` dict); 0.2.2 ships **27**. The trigger timing is an informational, machine-dependent benchmark of `bin/kurek-trigger` against a mock UDS daemon (`scripts/bench_trigger.py`), not end-to-end latency, so it is no longer quoted as a number. The 0.2.0 entry is left unchanged as a historical record.
- AGENTS.md: removed the "~0.8ms median" figure and describes the benchmark as above.

## [0.2.2] - 2026-10-28

### Added
- **Live voice on the phone (opt-in, `KUREK_LIVE_VOICE=1`):** `core/live_voice.py` serves `ws://<host>:8791/live` on the same hosts as HTTP and bridges the phone's 16 kHz PCM mic audio to xAI `grok-voice-3.0` (voice Sol), streaming 24 kHz PCM back. The xAI key stays on the PC. The endpoint needs the remote Bearer token, even on loopback, and rejects browser `Origin` requests.
- **Read-only tools during live calls:** system status, weather, web search, workstation radar, codebase reads, the vault, memory recall and process list. The daemon enforces the same allowlist again before anything runs.
- **Kurek Remote 0.2.2 (Android):** a redesign in the Omarchy Miasma theme (direction in `android/DESIGN.md`), a Talk live button with live transcripts, a Terminal action that opens herdr in Foot on the PC, and a 2x1 home-screen widget. A foreground service owns the call, so it keeps running with the screen off, with an ongoing notification, a timer and an End action.
- **Typed decisions, Jev first:** `core/decision_client.py` asks `typesafe/jev-1.14` on OpenRouter Decisions first, then `microsoft/microsoft-decision-1`, then a local heuristic, for screen context, code intent and dev-agent error judgments.
- **DeepSeek Search API for `web_search`:** DuckDuckGo and Google News RSS are the fallback.
- **OpenRouter speech-to-text by default:** new `OpenRouterSTT` in `core/stt.py` using `openai/whisper-large-v3-turbo` (override with `OPENROUTER_STT_MODEL`). `KUREK_STT_PROVIDER` defaults to `openrouter`; without an OpenRouter key the daemon falls back to Deepgram Nova-2, then local faster-whisper.
- **Spoken summaries and fallback replies:** when tools run but the model emits no spoken text, the daemon asks the model for a spoken summary of 1 to 4 sentences. Final fallbacks: an error notice, the short tool output itself, or "Done."
- **Android speak-aloud option:** Kurek Remote has a speaker toggle (persisted setting) and an `AudioQueuePlayer`; the daemon can stream synthesized TTS audio (base64) with each sentence to the phone.
- **File tool aliases:** `file_controller` accepts shell-style action aliases (`ls`, `mkdir`, `rm`, `mv`, `cp`, `cat`, `touch`, `save`, `search`, …) and shortcut sub-paths such as `desktop/notes.txt`.
- **Optional Claude Haiku brain:** `KUREK_LLM=haiku` switches reasoning and tool dispatch to Claude Haiku 5.5 (`ANTHROPIC_API_KEY` or `HAIKU_API_KEY`; `KUREK_HAIKU_MODEL`, `KUREK_HAIKU_EFFORT`, `KUREK_HAIKU_THINKING`). DeepSeek-Flash stays the default. `scripts/llm_ab.py` compares the two on the same prompts and tool loop. The startup log, the startup key check and stream errors name the active brain.
- **User rules (`core/rules.py`):** `~/.config/kurek/rules.toml` is injected into every prompt as `[USER RULES]`, word for word. Only the user edits it; a malformed file injects a visible notice instead of failing the reply.
- **Append-only memory ledger (`memory/ledger.py`):** `manage_memory` and Jev triage write to a `ledger` table that refuses updates and deletes. Every row needs a source, and a correction supersedes the old row (same key, or `replaces=<#id>`). The prompt carries only entries nothing has replaced.
- **Rule proposals (`core/proposals.py`):** the dream cycle, Jev boundary triage and `manage_memory` append candidate rules to `~/kurek_proposed_rules.md` instead of writing to `ALIGNMENT_SYNTHESIS.md`. Nothing reads that file into a prompt; the user promotes an entry by copying it into `rules.toml`.
- **Truncation warning:** a memory file longer than `KUREK_MEMORY_FILE_MAX_CHARS` now logs a warning naming the file, its size and the cap, instead of being cut without a trace.
- **Pinned rules:** a rule with `pin = true` in `rules.toml` is also re-sent verbatim right after the user's message each turn (model-facing only, never stored in history). Models weigh the end of the context most; pinning the brevity rule cut replies from 3 sentences to 2 in the rule-following check.
- **Rule-following check (`scripts/rules_eval.py`) and `docs/ANTI-DRIFT.md`:** adversarial prompts run through the real prompt and tool loop, scored by plain code with rules on and rules off. The document states the invariants and the rules for changing memory code.
- **27 tool modules:** new `codebase` (read-only repo inspection) and `run` (sandboxed, audited command runner) tools, up from 25 in 0.2.1.
- **`kurek doctor` and `kurek logs [-f]`:** doctor checks API keys (presence only, never values), required binaries (mpv, wtype, playerctl, spd-say, …), mic, socket, service and `.env` permissions; logs follows journald or the fallback log.
- **Local speech-to-text fallback:** when cloud STT (OpenRouter/Deepgram) fails, the utterance is retried on local faster-whisper (`FallbackSTT`). `KUREK_WHISPER_MODEL` selects the local model (default `base.en`).
- **Startup readiness notification:** one notification lists missing keys, missing voice/player binaries or a failed speech model.
- **Installer flags:** `--no-enable`, `--dev`, `--locked`; `requirements-dev.txt` and a pinned `requirements.lock`.
- **Weather with real data:** `weather_report` returns current conditions and today's/tomorrow's forecast from wttr.in instead of opening a browser search.

### Changed
- **Longer spoken answers:** up to 6 sentences and 150 words for explanations and conversation; facts and action results stay short.
- **Tool rounds:** up to 15 tool rounds per request (was 5).
- **Streaming TTS queue:** sentences are synthesized by a worker thread and appended to mpv, so the DeepSeek stream no longer pauses for each xAI TTS round trip. Remote requests with `stream_audio` synthesize each sentence once (previously twice when the PC also spoke).
- **Wayland-native input:** typing, hotkeys and key presses use `wtype` on Wayland (pyautogui fallback); brightness uses `brightnessctl` only; wallpaper uses Omarchy's `omarchy-theme-bg-set`, then hyprpaper/swaybg; YouTube play/pause uses `playerctl`.
- **Installer enables the service:** `install_linux.sh` runs `systemctl --user enable --now kurek.service` and respects `XDG_CONFIG_HOME`.
- **`launch_kurek.sh`:** exports `.env` (so `KUREK_*` settings work outside systemd), logs to `~/.local/state/kurek/daemon.log` (mode 600, rotated at 5 MB) instead of `/tmp`, and refuses to start a second daemon while `kurek.service` is active.
- **State toasts:** only Listening and Thinking by default (`KUREK_STATE_NOTIFY=minimal|all|off`).
- **Prompt size cap:** each memory file injected into the system prompt is capped (`KUREK_MEMORY_FILE_MAX_CHARS`, default 6000; head + newest tail). Jev no longer appends duplicate memory/boundary lines.
- **Dream cycle:** reflects on *yesterday's* log, runs on the first hourly check after 05:00 (catching up if the PC was off), and preserves accumulated boundaries in `ALIGNMENT_SYNTHESIS.md` under "Persistent Boundaries".
- **Confirmation reply:** "Confirmed, doing it now." instead of "Confirmed and executed."; confirmation failures are logged and notified.
- **DeepSeek client:** default model fallback is `deepseek-flash` (served by DeepSeek-V4.1-Flash-1017; `deepseek-chat` is discontinued). One retry on connection errors / 429 / 5xx before any token; the stream connection is always closed.
- **CI:** installs `requirements-dev.txt`, runs `ruff check .` with the rules in `pyproject.toml` (E9, F63, F7, F82, F401, B006, B008), and shellchecks the CLI and launcher too.
- **Longer listen window:** 2.2 s of silence to auto-submit (was 1.2 s), 30 s maximum recording (was 12 s), 6 s initial-silence timeout (was 4.5 s). Tunable via `KUREK_SILENCE_DURATION`, `KUREK_MAX_RECORD_SECONDS`, `KUREK_INITIAL_SILENCE_TIMEOUT`.

### Fixed
- **Haiku brain after tool-heavy prompts:** the final round keeps the tool list and forbids calls with `tool_choice`, so Anthropic no longer rejects the replayed thinking blocks.
- **Phone stream:** `/remote/prompt` closes the connection after `done` instead of holding it open.
- **Tests:** a shell that exports the live `.env` no longer changes test results.
- **Dream cycle:** a failed LLM call no longer writes a placeholder dream that blocked retries for the day, and the nightly run no longer wipes boundaries added during the day.
- **Speech recognition errors:** network/auth failures no longer sound like "I didn't hear anything"; Kurek says recognition failed and notifies.
- **mpv chunk cleanup:** queued sentence clips of a long reply are no longer deleted after 30 s before they play.
- **History and memory writes** are atomic (temp file + rename) and private (mode 600): `kurek_history.json`, `long_term.json`, `api_keys.json`, `clipboard_history.json`, the SQLite memory db.
- **Hyprland watcher** re-resolves its socket after Hyprland restarts.
- **Honest tool results:** `send_message` no longer claims delivery; `computer_control` checks the focus command's exit code; `dream_tool` reports failures.
- **Timeouts** on one-shot subprocess calls in actions (grim, pip, notify, …) and on Gemini client calls (60 s).
- **mpv:** reap the audio sink instead of leaking an idle mpv per run.
- **PortAudio:** import PortAudio before numpy to avoid a daemon startup deadlock.

### Security
- **`rename_file`** rejects path separators and names that would leave `$HOME`.
- **Archive extraction** uses tar `filter="data"`, so crafted archives can't write outside the destination.
- **Gated unrestricted shell:** the `run` tool's unrestricted shell mode is OFF unless `KUREK_REMOTE_ALLOW_EXEC=1`; `KUREK_UNRESTRICTED_SHELL` / `KUREK_ALLOW_SHELL` alone no longer enable it. A loud warning is logged when enabled, and the tool description and prompts only mention arbitrary execution when it is on.

### Removed
- **Mouse-1 decision provider** and `MOUSE_API_KEY`; Gemini no longer classifies code intent.
- **Unsupported code:** the legacy PyQt6 GUI (`legacy/`), the macOS menu bar (`menubar/`), the unused Ollama/OpenAI-local LLM stack, Edge/Kokoro/ElevenLabs/Vosk speech engines, `core/wake_word.py`, `core/plugin_loader.py`, `core/audio_devices.py`, `core/installer.py`, the tool-less `actions/background_monitor.py`, `proactive.py`, `screen_processor.py`, and Windows-only branches in `actions/`.
- **Unused dependencies:** pytest moved to `requirements-dev.txt`; `beautifulsoup4` removed.
- **tkinter dialog:** `actions/youtube_video.py` no longer opens a tkinter URL dialog (Kurek is headless); it replies asking for the URL instead.

## [0.2.1] — 2026-10-13

Targeted architectural and stability refactor for Arch Linux / Omarchy Quattro (Hyprland, Ghostty, Herdr, Fish) and Android Remote client over Tailscale.

### Fixed
- **Zero-config `.env` installs:** Eliminated raw `config/api_keys.json` file reads across `actions/` modules (`dev_agent`, `code_helper`, `flight_finder`, `desktop`, `file_processor`, `computer_settings`, `computer_control`, `youtube_video`). All key access routes through `memory.config_manager.get_gemini_key()`, checking environment variables first.
- **Hyprland window controls:** Replaced X11 `pyautogui` hotkeys in `actions/computer_settings.py` with native `hyprctl dispatch` calls (`cyclenext`, `killactive`, `fullscreen 0`, `movewindow l/r`, `dpms off`), native Wayland screenshot capture via `grim`, and screen locking via `hyprlock`/`swaylock`.
- **Systemd 600M envelope safety:** Protected `kurek.service` against OOM killer by passing low-memory Chromium flags in `browser_control.py` and delegating heavy builds in `dev_agent.py` to `systemd-run --user --scope`. Added 120s timeout guard in `ActionRegistry.run`.
- **Private lock decoupling:** Removed private `_lock` import in `actions/background_monitor.py`, replacing with thread-safe public state persistence helpers.

### Added
- **Omarchy terminal & shell support:** Added first-class prioritization for `ghostty` and `herdr` in `_LINUX_TERMINAL_FALLBACKS` and `_APP_ALIASES`, and added `fish` shell execution support. Task manager launches `btop`/`htop` directly inside `ghostty` or `herdr`.
- **SQLite WAL + FTS5 memory backend:** Upgraded `memory/memory_manager.py` from full-file JSON rewrites to SQLite in WAL mode with FTS5 full-text search indexing (`entries_fts`), automatic migration of legacy `long_term.json`, and 100% backward-compatible API.
- **Remote prompt SSE optimization:** Muted local PipeWire workstation TTS during remote phone queries over Tailscale `/remote/prompt` unless `speak_local` is requested.
- **Conversational confirmation gate:** Wired `core/confirm.py` with `notify-send` and conversational affirmative/negative turn detection in `kurek_daemon.py` to confirm or abort pending destructive operations.

### Changed
- **LLM prompt alignment:** Synchronized `core/prompt.txt` with actual runtime tool schemas (`screen_vision`, `manage_memory`, `system_monitor`, `vault_knowledge`, `workstation_radar`, `process_sentinel`), removing legacy PyQt6 HUD instructions and defunct tool names.

## [0.2.0] — 2026-10-10

Hardening and measurement release on top of the v0.1.0 headless daemon:
audit fixes for UDS/streaming, portable install + socket security, lazy
Hyprland geometry, timing/bench tooling, and docs that claim only measured figures.

### Fixed

#### Stability (since v0.1.0 / Oct 8)
- Resolve daemon unbound-local error paths, dream-cycle return handling, and F821 undefined symbols; align dependencies ([`d863922`](https://github.com/nodaysidle/kureksistant/commit/d863922)).
- Canonicalize the headless daemon path; quarantine the unsupported PyQt6 GUI under `legacy/`; add CI smoke tests, `SECURITY.md` / `NOTICE`, and packaging deny-list ([#2](https://github.com/nodaysidle/kureksistant/pull/2)).
- Correct documented RAM footprint and tool count (24) ([#3](https://github.com/nodaysidle/kureksistant/pull/3)).

#### Audit PR #4 — stream/UDS correctness
- Keep UDS state-event subscribers open until disconnect; prune dead peers on broadcast failure (Waybar/AGS no longer see silent sockets).
- Raise systemd memory envelope for Whisper footprint; later refined in #6.
- Normalise dream-cycle LLM responses (`str` / `dict` / `None`) via shared helper.
- Catch DeepSeek stream/network errors, always reset daemon state to IDLE.
- Stop reporting failures as success (“All set.”); handle `error` / empty stream honestly without fake history.

#### Audit PR #5 — install paths & socket security
- Template `desktop/kurek.service` with `@KUREK_DIR@`; installer renders into `~/.config/systemd/user/` and writes `$XDG_CONFIG_HOME/kurek/install_path`.
- C trigger reads `install_path` for launcher fallback; polls the UDS socket instead of a fixed 250ms sleep.
- Idempotent `install_linux.sh`: pacman dep checks (mpv, grim, wl-clipboard, libnotify, gcc), hard-fail if gcc missing, always refresh venv + Playwright Chromium, install `~/.local/bin/kurek-trigger`.
- UDS: mode `0600`, `SO_PEERCRED` same-uid gate, no `/tmp/kurek.sock` fallback (private `~/.cache/kurek/run` when `XDG_RUNTIME_DIR` unset).
- C trigger: JSON escaping, 500ms send/recv timeouts, clean `-Wall -Wextra` build.

#### Audit PR #6 — Hyprland / timing / docs
- Parse Hyprland `.socket2` focus/open/move/workspace events into RAM; **no** `hyprctl` on every focus change.
- Lazy geometry: one seed `hyprctl` at startup + one fetch per vision/screen-capture request.
- `KUREK_TIMING=1` per-query ms marks (trigger receipt, first DeepSeek token/sentence, first `speak_chunk`, mpv `loadfile`).
- `scripts/bench_trigger.py` (mock UDS daemon); CI prints median/p95 without gating.
- Sentence splitter: flush short trailing text; do not split on `Dr./Mr./Mrs./Ms./e.g./i.e./etc./vs./St.` or decimals.
- `MemoryHigh=512M` / `MemoryMax=600M` (observed peak ~452MiB with faster-whisper).
- Docs claim only measured figures: ~450MB peak RAM, ~0.8ms median trigger, 24 tools.

### Added
- Streaming sentence-buffered DeepSeek TTS pipeline, UDS trigger client (`bin/kurek-trigger`), persistent PipeWire mpv sink, and Hyprland `.socket2` watcher (pre-audit feature land ahead of #4–#6).

### Changed
- Default packaging `VERSION` and CI dry-run packaging bump to **0.2.0**.

## [0.1.0] — 2026-10-06

Initial public release: headless Linux/macOS daemon, Middle-Click summon, DeepSeek-Flash + xAI Grok (Sol) voice, Muse Memory / TypeSafe Jev, 24 tool modules, portable installer, and CC BY-NC 4.0 licensing (derived from FatihMakes' JARVIS).

[0.2.1]: https://github.com/nodaysidle/kureksistant/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/nodaysidle/kureksistant/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/nodaysidle/kureksistant/releases/tag/v0.1.0

