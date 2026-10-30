# CLAUDE.md — kurekizmo (Kureksistant)

## 1. Canary — read first
- Start every reply with `ALAN`. No exceptions, including one-line answers.
- If `HANDOFF.md` exists, read it before doing anything else.
- When the session is long or you cannot clearly recall earlier work, say so, then write `HANDOFF.md` before continuing.
- Consult `/home/arch/dev/nodaysidle/nodaysidle-knowledge` (`sessions/LATEST.md` and `wiki/`) as the central source of truth for ecosystem decisions.

## 2. Execution Posture (Opus 5.5 Directives)
- Prioritize direct code execution and tool use over conversational narration.
- Do not provide mid-task status summaries or narrate routine file reads.
- Continue autonomously through multi-step refactoring workflows until complete; only pause if blocked by a critical ambiguity.
- The smallest change that fully satisfies the task (YAGNI). Never introduce unsolicited refactoring, unnecessary abstraction layers, or extra threading wrappers.

## 3. Command Matrix
*Execute these exact commands:*

| Command | Purpose |
|---------|---------|
| `python3 kurek_daemon.py` | Run daemon locally |
| `./launch_kurek.sh` | Launch daemon with standard environment setup |
| `ruff check .` | Run Ruff linter (enforced in CI) |
| `ruff format --check .` | Check code formatting (double quotes, 88 cols) |
| `pytest -q` | Run unit test suite (headless safe) |
| `bash scripts/verify.sh` | Full isolated self-check (never touches live DB/port) |

## 4. Architecture & Runtime Constraints
- **Product**: Headless voice assistant daemon for Omarchy / Hyprland Linux desktop with optional Android phone remote over Tailscale.
- **Stack**: Python 3.10+, SQLite (`memory/`), PyAudio/PortAudio, DeepSeek-Flash (V4.1), xAI Grok TTS (`Sol`), Ruff, Pytest.
- **Live Memory DB Isolation**: Never open or mutate `memory/kurek_memory.db` during test runs or ad-hoc test scripts. Tests must use isolated temporary SQLite databases.
- **Concurrency & Loader Lock**: Always export `OPENBLAS_NUM_THREADS=1` before importing `kurek_daemon` in test environments to prevent OpenBLAS / PortAudio dlopen deadlocks.
- **Desktop Interop**: Hyprland window controls use `hyprctl dispatch`. The active terminal is Ghostty; Herdr runs inside Ghostty.
- **Display & Headless Tests**: PyAutoGUI and mouse actions require a running X/Wayland display or `xvfb-run`.
- **Port Safety**: Default live daemon port is `8790`. Verification and mock servers must use non-live throwaway ports (e.g. `18791`).

## 5. Negative Constraints (Anti-Defaults)
- Never leave mock stubs or placeholder comments (`# TODO: implement`).
- Never run tests without database isolation.
- Do not introduce GUI libraries; Kurek is strictly a headless system daemon.

## 6. Modular Imports
@AGENTS.md
@README.md

## Agent skills

### Issue tracker

GitHub Issues on nodaysidle/kureksistant, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five labels: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `GLOSSARY.md` + `docs/adr/` (created lazily). See `docs/agents/domain.md`.
