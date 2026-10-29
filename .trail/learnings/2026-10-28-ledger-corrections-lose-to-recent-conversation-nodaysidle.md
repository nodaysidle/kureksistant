---
title: Ledger corrections lose to recent conversation history until it is cleared
author: Nodaysidle
date: 2026-10-28
branch: fix/history-tool-record
cost: { minutes: 4, tokens: 33195 }
id: l-cc65b136
scope: repo
status: active
src: agent
touches:
  - kurek_daemon.py
---

## Decided
2026-10-29: after superseding ledger rows (#13/#16 -> #18/#20), Kurek still answered with the old facts, because kurek_daemon.py puts the last 10 turns of memory/kurek_history.json into every prompt, and those turns repeated the stale values. Clearing the history file (daemon stopped, file backed up, written as []) fixed the answer immediately.

## Watch out
- memory.ledger.append skips text it already has and returns the existing id. When retiring several rows, give each superseding row its own wording, or only the first gets retired.
- Retiring N rows always leaves N active rows, because each row supersedes exactly one. Keep the retirement text short.
- The daemon rewrites kurek_history.json, so stop it before editing that file.
