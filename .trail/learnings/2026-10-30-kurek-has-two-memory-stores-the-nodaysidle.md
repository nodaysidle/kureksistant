---
title: "Kurek has two memory stores the anti-drift doc does not list, and daily logs never reach the prompt"
author: Nodaysidle
date: 2026-10-30
branch: fix/history-tool-record
cost: { minutes: 2, tokens: 24027 }
id: l-33adecbb
scope: repo
status: active
src: agent
touches:
  - docs/ANTI-DRIFT.md
  - kurek_daemon.py
  - actions/memory_tool.py
  - memory/memory_manager.py
---

## Watch out
- The old personal-context store (the `entries` table) is empty and nothing writes to it any more, yet every prompt still carries its empty `[USER PERSONAL CONTEXT & IDENTITY]` header. Recall still searches it.
- `~/memory/bank/*.md` is searched by `manage_memory` recall only. It is not in docs/ANTI-DRIFT.md's store table.
- Daily logs (`~/memory/YYYY-MM-DD.md`) are written every turn but never injected into the prompt. Only Jev triage and the dream cycle read them. Don't assume Kurek "remembers" what's only in a daily log.
- 2026-10-30: a grilling session is open on whether to retire both stores and on the meaning of "memory" (proposed: learned memory, the ledger, only). Not decided yet.

In the code: kurek_daemon.py:985 (format_memory_for_prompt(load_memory())), kurek_daemon.py:1295 (_append_daily_memory), actions/memory_tool.py manage_memory recall (bank + search_memory), memory/memory_manager.py remember/update_memory/save_session_summary have no callers.
