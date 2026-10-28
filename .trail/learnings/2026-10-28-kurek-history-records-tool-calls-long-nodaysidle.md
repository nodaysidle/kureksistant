---
title: Kurek history records tool calls; long pinned rules crowd out the language rule
author: Nodaysidle
date: 2026-10-28
branch: main
cost: { minutes: 20, tokens: 129469 }
id: l-c49ba28b
scope: repo
status: active
src: agent
touches:
  - kurek_daemon.py
  - tests/test_history_tools.py
---

## Decided
- 2026-10-28: assistant turns in memory/kurek_history.json carry a `tools` list; `history_message()` in kurek_daemon.py renders it as "(Tool calls in that turn, recorded by the daemon: ...)" when building messages. Before this, history was text-only, so under the "verify in the current turn" rules Kurek falsely confessed it had not searched (Gemini 4 Carbon, 21:50 log shows two web_search calls before the reply). The reply clean-up strips that note if the model copies it.
- rules.toml rule `mandatory-web-search` (owner's repair spec, verbatim) is UNPINNED on purpose.
## Tried and ruled out
- Pinning `mandatory-web-search`: other-language-gets-english dropped to 15/20 (18/18 without). Unpinned: 12/12, and every web_search case plus a multi-turn re-search check still pass on Haiku.
## Watch out
- rules_eval.py is single-turn only; it cannot see history bugs. Seed engine.history by hand (see llm_ab.build_engine) for multi-turn checks.
- Before pinning another long rule, re-run the language case at 6+ reps with and without.
