# CLAUDE.md — kurekizmo (Kureksistant)

## Canary — read first

- Start every reply with `ALAN`. No exceptions, including one-line answers.
- If `HANDOFF.md` exists, read it before doing anything else.
- When the session is long or you cannot clearly recall earlier work, say so, then write `HANDOFF.md` before continuing.
- Consult `/home/arch/dev/nodaysidle/nodaysidle-knowledge` (`sessions/LATEST.md` and `wiki/`) as the central source of truth for ecosystem decisions.

## Project Overview
- **Product**: Headless voice assistant daemon for Omarchy / Hyprland Linux desktop with optional Android phone remote over Tailscale.
- **Stack**: Python 3.10+, SQLite (`memory/`), PyAudio/PortAudio, DeepSeek-Flash / Claude Haiku 5.5, xAI Grok TTS (`Sol`), Ruff, Pytest.
- **Design Philosophy**: Headless system daemon with zero GUI. Activated by middle-click mouse trigger, hotkey, or `kurek` CLI.

## Common Commands

| Command | Purpose |
|---------|---------|
| `python3 kurek_daemon.py` | Run daemon locally |
| `./launch_kurek.sh` | Launch daemon with standard environment setup |
| `ruff check .` | Run Ruff linter (enforced in CI) |
| `ruff format --check .` | Check code formatting (double quotes, 88 cols) |
| `pytest -q` | Run unit test suite (headless safe) |
| `bash scripts/verify.sh` | Full isolated self-check (never touches live DB/port) |

## Architecture & References
@AGENTS.md
@README.md

## Critical Safety & Runtime Constraints
- **Live Memory DB Isolation**: Never open or mutate `memory/kurek_memory.db` during test runs or ad-hoc test scripts. Tests must use isolated temp SQLite databases.
- **Concurrency & Loader Lock**: Always export `OPENBLAS_NUM_THREADS=1` before importing `kurek_daemon` in test environments to prevent OpenBLAS / PortAudio dlopen deadlocks.
- **Desktop Interop**: Hyprland window controls use `hyprctl dispatch`. The terminal is Velocitty since 2026-10-29 (Foot before it, Ghostty retired 2026-10-23); Herdr runs inside Velocitty.
- **Display & Headless Tests**: PyAutoGUI and mouse actions require a running X/Wayland display or `xvfb-run`.
- **Port Safety**: Default live daemon port is `8790`. Verification and mock servers must use non-live throwaway ports (e.g. `18791`).
