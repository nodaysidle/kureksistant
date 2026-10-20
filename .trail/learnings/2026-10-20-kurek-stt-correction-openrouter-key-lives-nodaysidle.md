---
title: "Kurek STT correction: OpenRouter key lives in config/api_keys.json"
author: Nodaysidle
date: 2026-10-20
branch: ship/v0.2.2
cost: { minutes: 6, tokens: 27386 }
touches:
  - AGENTS.md
  - actions/file_processor.py
  - core/dream_cycle.py
  - tests/test_actions_hardening.py
  - tests/test_core_dream_cycle.py
  - config/api_keys.json
---

## Decided
- Correction to the v0.2.2 quality-pass learning: the owner DOES have a cloud STT key. OPENROUTER_API_KEY is stored in config/api_keys.json (not .env), so the daemon uses OpenRouter Whisper (multilingual) first and local faster-whisper base.en only as the FallbackSTT fallback.

## Watch out
- When checking which keys are configured, read both .env and config/api_keys.json (memory/config_manager.load_api_keys merges them); `kurek doctor` does this.
