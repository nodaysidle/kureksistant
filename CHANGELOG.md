# Changelog

All notable changes to Kureksistant (Kurek) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project approximates [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Documentation
- **Correction to the 0.2.0 entry below:** it lists "~0.8ms median trigger, 24 tools" as measured figures. The bundled tool count is **25**: 25 `actions/*.py` files define a `TOOL` dict, and `core/action_loader.discover_actions()` loads 25 at runtime. The trigger timing is an informational, machine-dependent benchmark of `bin/kurek-trigger` against a mock UDS daemon (`scripts/bench_trigger.py`), not end-to-end latency, so it is no longer quoted as a number. The 0.2.0 entry is left unchanged as a historical record.
- AGENTS.md: removed the "~0.8ms median" figure and describes the benchmark as above.

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

