---
title: "Prompt wording cannot make learned memory beat the user's own words in recent history"
author: Nodaysidle
date: 2026-10-28
branch: fix/history-tool-record
cost: { minutes: 8, tokens: 32705 }
id: l-2c256bf4
scope: repo
status: active
src: agent
touches:
  - memory/ledger.py
  - kurek_daemon.py
---

## Tried and ruled out
2026-10-29, Haiku, multi-turn check (last 10 history turns seeded with the user saying "Velocity" and "Downloads/Kurek/", ledger corrected to Velocitty and ~/Downloads/KUREK/NOTES):
- Baseline: 0/4.
- Adding to ledger PROMPT_HEADER "earlier turns may be outdated; the entry wins, even over what the user said then": 0/4.
- Adding a daemon note after the current user message (next to the pinned rules) saying rules and memory beat earlier turns: 0/4. The model calls the in-chat user statement "your latest instruction" and asks to reconcile.
Both were reverted.

## Decided
The conflict only happens when memory is corrected outside the chat (by hand or by an agent) while the chat still holds the old statement. Then clear memory/kurek_history.json (stop the daemon, back the file up first). Corrections made inside the chat already agree with the history.

## Watch out
Speech-to-text writes "Velocitty" as "Velocity". For a voice reply the spelling does not matter.
