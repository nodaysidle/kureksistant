---
title: "Kurek v0.2.2 quality pass: owner policy, TTS queue, STT fallback, dream cycle"
author: Nodaysidle
date: 2026-10-20
branch: ship/v0.2.2
cost: { minutes: 35, tokens: 329729 }
touches:
  - AGENTS.md
  - menubar/kurekbar
  - ".env.example"
  - CHANGELOG.md
  - README.md
  - ".github/workflows/ci.yml"
  - bin/kurek
  - install_linux.sh
  - launch_kurek.sh
  - pyproject.toml
---

## Decided
- Owner policy: Kurek keeps full power (file writes anywhere in $HOME, code execution, remote exec over Tailscale with KUREK_REMOTE_ALLOW_EXEC=1). Do NOT add sandboxing, remote block-list entries, confirmation prompts or restricted safe roots. Only hardening that doesn't change the workflow (e.g. rename stays in $HOME, tar filter="data", honest tool results).
- `deepseek-flash` is DeepSeek's official unversioned alias (served by DeepSeek-V4.1-Flash-1017 since 2026-10-17). There is no valid `deepseek-v4.1-flash` model id; `deepseek-chat` is discontinued.
- TTS: core/tts.py TTSPlayer.enqueue/wait_stream/begin_stream — one worker synthesizes sentences in order and appends to mpv; remote stream_audio reuses the same bytes. Daemon waits for wait_stream before SSE "done".
- STT: cloud engines raise core.stt.STTError; FallbackSTT retries on local faster-whisper (KUREK_WHISPER_MODEL, default base.en). Owner has no cloud STT keys -> local whisper only.
- Dream cycle reflects on yesterday, writes nothing on LLM failure, preserves "Persistent Boundaries".
- Daemon split of kurek_daemon.py was considered and skipped (no user-visible benefit).

## Tried and ruled out
- Making faster-whisper / playwright optional extras: owner uses both, so they stay in requirements.txt.

## Watch out
- The live daemon must be restarted (`systemctl --user restart kurek`) to pick up code changes.
- test fakes for TTS (tests/test_stream_errors.py _FakeTTS) must implement begin_stream/enqueue/wait_stream/synthesize.
- Never run tests that save history without pointing engine.history_file at tmp_path — it is the owner's live memory/kurek_history.json.
- scripts/verify.sh tests a COMMIT (git archive), not the working tree.
