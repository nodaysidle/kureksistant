# Restore: decision layer + listening window (2026-10-22)

Two independent changes landed in one working-tree edit. Either can be reverted
without the other, and both can be disabled by environment variable without
touching code.

| Change | Files |
| --- | --- |
| **A. Decision layer** — Mouse-1 first, then Decision-1, then Jev; one `decide()` for `needs_screen` and `intent`; per-call timeout + total budget | `core/decision_client.py`, `kurek_daemon.py`, `actions/code_helper.py`, `memory/config_manager.py` |
| **B. Listening window** — longer, adaptive pause; 3-minute ceiling | `kurek_daemon.py` |
| Docs / tests | `.env.example`, `tests/test_decision_screen_context.py` (updated), `tests/test_decision_providers.py` + `tests/test_listen_window.py` (new) |

Pre-change copies, hashes, git HEAD and measured latencies are in
`.backups/2026-10-22-decision-listen/` (`manifest.json`).

## Before you restore

The working tree was already dirty before this change (`AGENTS.md`,
`core/prompt.txt`). **Do not** `git checkout .` — it would discard work that is
not part of this change. Restore from the backup copies, not from git.

`.backups/` is gitignored, so `git clean -xfd` deletes the restore point. If you
need it to survive a clean, copy the directory outside the repo first.

## Full restore (both changes)

```bash
cd /path/to/kurekizmo
B=.backups/2026-10-22-decision-listen
for f in core/decision_client.py kurek_daemon.py actions/code_helper.py \
         memory/config_manager.py .env.example tests/test_decision_screen_context.py; do
  cp -p "$B/$f" "$f"
done
rm -f tests/test_decision_providers.py tests/test_listen_window.py
rm -f RESTORE-DECISION-LISTEN.md          # optional: this file
rm -rf "$B"
```

Then verify:

```bash
.venv/bin/python -m pytest -q          # expect 685 passed, 1 skipped on a clean rollback
git status --porcelain                 # only the pre-existing AGENTS.md / prompt.txt edits
```

The rollback suite count is lower because the two new test files are removed
(verified on a copy: 684 passed plus one git-dependent test that only runs
inside a real checkout). The pre-change hash for every restored file is
`sha256_restore` in `manifest.json`; check with `sha256sum <file>`.

## Partial restore

- **Only the listening change:** restore `kurek_daemon.py` and delete
  `tests/test_listen_window.py`. This also reverts the decision wiring in the
  daemon, so restore `core/decision_client.py` first if you keep change A.
- **Only the decision change:** restore `core/decision_client.py`,
  `actions/code_helper.py`, `memory/config_manager.py`,
  `tests/test_decision_screen_context.py`, and the `_check_auto_screen_context`
  body in `kurek_daemon.py`; delete `tests/test_decision_providers.py`.

## Disable without restoring

```bash
KUREK_DECISION_INTENT=0     # intent routing falls back to Gemini, then structural hints
KUREK_AUTO_SCREEN=0         # no needs_screen call, no screenshot
# unset MOUSE_API_KEY       # provider order becomes Decision-1 → Jev
KUREK_DECISION_TIMEOUT=1.5          # per-call cap (seconds)
KUREK_DECISION_BUDGET=2.5           # total cap for needs_screen; then heuristic
KUREK_DECISION_INTENT_BUDGET=5.0    # total cap for intent routing (inside a tool call)
```

Listening, no code change:

```bash
KUREK_SILENCE_DURATION=2.2      # back to the old base pause
KUREK_SILENCE_GROWTH=0          # disable the adaptive extra entirely
KUREK_SILENCE_MAX_EXTRA=0
KUREK_INITIAL_SILENCE_TIMEOUT=6.0
KUREK_MAX_RECORD_SECONDS=30.0
KUREK_AUTO_SUBMIT=off           # only a click stops; the ceiling still applies
```

## What changed, exactly

**A. `core/decision_client.py`** is now a provider table
(`mouse` → `decision1` → `jev`) over one request shape
`{model, state, questions}` → `{answers}`.

- `query_decision(state, questions)` returns a `Decision(provider, answers, usage)`.
- `query_needs_screen(prompt) -> (prob|None, engine)`; `abstain: true` and
  missing answers both yield `(None, "Heuristic")` so the daemon skips capture.
- `classify_intent(description, file_path, code) -> (intent|None, engine)`; the
  seven labels live in `INTENT_LABELS`.
- Per-provider timeouts: mouse 1.5 s, Decision-1 1.5 s, Jev 2.0 s; total
  `KUREK_DECISION_BUDGET` (default 2.5 s) is split across them. Intent routing
  uses `KUREK_DECISION_INTENT_BUDGET` (default 5.0 s) because it runs inside a
  tool call rather than in front of the first token.
- `KUREK_DECISION_TIMEOUT` caps a single call and overrides every provider.
- Legacy entry points kept: `query_openrouter_needs_screen`,
  `parse_needs_screen_noul`, `auto_screen_enabled`, `decision_timeout_s`.
- The Jev fallback no longer needs `typesafe_sdk`; it is plain HTTPS.
- `KUREK_STT_LANGUAGE` pins the recogniser for OpenRouter, Deepgram, and the
  local Whisper fallback (empty or `auto` keeps per-clip detection). Reverting
  means deleting that line from `.env`; no code change.
- Never logs keys or response bodies; failures print `TypeError: …` with the
  key redacted.

**A. `kurek_daemon.py`** — `_check_auto_screen_context` is one call to
`query_needs_screen(user_prompt)`; the engine name is what gets logged.
Threshold is unchanged (`SCREEN_CONTEXT_THRESHOLD = 0.75`).

**A. `actions/code_helper.py`** — `_detect_intent` asks the decision layer first,
then Gemini with the existing prompt, then the structural hints.

**B. `kurek_daemon.py`** — `SILENCE_DURATION` 2.2 → 4.0 s, plus
`effective_silence_window()`: the pause grows by `SILENCE_GROWTH` (0.6) per
second already spoken, capped at `SILENCE_MAX_EXTRA` (10.0 s). A long question
gets up to 14 s of pause; a two-word answer keeps 4 s. `MAX_RECORD_SECONDS` 30 →
180, `INITIAL_SILENCE_TIMEOUT` 6 → 30.

The VAD gates are now relative to the measured room instead of absolute:
speech onset `max(0.006, noise_floor × 2.0)` (was `max(0.045, …)`), continuation
`max(0.0025, noise_floor × 1.3)` (was `max(0.035, …)`), and the floor calibrates
fast for the first `KUREK_VAD_WARMUP_S` (0.3 s) so a loud fan and a silent desk
mic are both handled. ``KUREK_AUTO_SUBMIT=off`` skips both the pause rule and the
no-speech rule; only the click and the ceiling stop a session. `KUREK_AUTO_SUBMIT=off` removes the pause
rule entirely, leaving the click (and the 180 s ceiling) as the only stop.

**Unchanged:** the middle-click contract. `mouse:274` runs `kurek toggle`
(`KurekEngine.toggle`), so press starts listening and **press again stops and
submits immediately**. The watchdog is only a safety net for a forgotten
session; change B widens it, it does not change the manual path.

## Measured, 2026-10-22, this desktop

| Path | Latency |
| --- | --- |
| TLS handshake to `api.mouse.dev` | ~85 ms |
| Mouse-1 decision, warm | 450–790 ms |
| Mouse-1 decision, cold | > 2500 ms (observed once) |
| Decision-1 (`needs_screen`) | ~1400 ms |
| Jev | ~750–2700 ms |

The published "~100 ms" is server-side US-East. End-to-end from Europe is
~0.5 s warm, so the mouse timeout is 1.5 s, not the 0.45 s the docs imply. If a
future change makes the gate feel slow, the first thing to try is connection
reuse (urllib opens a new TLS connection per call).

## Tests

`tests/test_decision_providers.py` covers provider order, fallback, abstain,
timeout override, budget exhaustion, choice rejection and key redaction.
`tests/test_listen_window.py` covers the adaptive window, the 3-minute ceiling,
and the toggle contract (second press stops).

`tests/test_decision_screen_context.py` was updated to patch the new
`daemon.query_needs_screen` seam; the old assertions about the in-daemon Jev
fallback moved into the new provider tests.
