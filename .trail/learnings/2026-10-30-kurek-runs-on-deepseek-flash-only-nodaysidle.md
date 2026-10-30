---
title: Kurek runs on DeepSeek-Flash only; the Claude Haiku brain was removed
author: Nodaysidle
date: 2026-10-30
branch: fix/history-tool-record
cost: { minutes: 16, tokens: 126613 }
id: l-b65b5a7e
scope: repo
status: active
src: agent
touches:
  - GLOSSARY.md
  - README.md
  - scripts/verify.sh
  - core/llm_client.py
  - kurek_daemon.py
  - scripts/rules_eval.py
  - scripts/llm_ab.py
---

## Decided
2026-10-30, owner: DeepSeek-Flash is Kurek's only brain. The Haiku option was removed completely: its code path, key lookup, tests, the anthropic package and the KUREK_LLM switch. `deepseek-flash` is DeepSeek's alias for the newest Flash model (DeepSeek-V4.1-Flash, checked against the /models endpoint on 2026-10-30); the only other model listed was V4-Pro.

## Tried and ruled out
Keeping Haiku as a dormant option with only the stale config lines removed: the owner chose full removal.

## Watch out
- The rules eval used to be hardcoded to Haiku, so every score before 2026-10-30 (including the 60/60 run) measured Haiku, not the live brain. Compare only runs made after the switch.
- scripts/verify.sh tests the last commit, not the working tree. With anthropic uninstalled from .venv, it fails on old commits that still have the Haiku tests.
- The owner's other apps still use ANTHROPIC_API_KEY; only Kurek's own .env lost its Haiku lines.

In the code: core/llm_client.py (brain_name now constant), kurek_daemon.py brain_key_problem, scripts/rules_eval.py + scripts/llm_ab.py run_one(engine, prompt), requirements.txt/requirements.lock (recompiled with the command in the lock header).
