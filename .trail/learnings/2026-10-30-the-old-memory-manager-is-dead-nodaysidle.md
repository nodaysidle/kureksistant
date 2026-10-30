---
title: "The old memory manager is dead code apart from the database path, and the legacy JSON import no longer runs"
author: Nodaysidle
date: 2026-10-30
branch: main
cost: { minutes: 3, tokens: 108301 }
id: l-a9196e8b
scope: repo
status: active
src: agent
touches:
  - memory/memory_manager.py
  - memory/ledger.py
  - kurek_daemon.py
  - actions/memory_tool.py
---

## Watch out
- Outside tests, nothing calls the old memory manager's functions (load, remember, search, prompt formatting, sessions, monitors). Production uses only its database path, which the ledger connects to.
- The legacy long_term.json import only runs inside load_memory(), and nothing calls that anymore. The README line saying it "is imported automatically on first start" is stale.
- Reading Hermes USER.md/MEMORY.md and ~/MEMORY.md is duplicated: the daemon's prompt build and manage_memory recall each resolve the Hermes dir, split on §, and cap separately. Change both, or extract them first.

In the code:
- memory/memory_manager.py: only DB_PATH is used in production (memory/ledger.py _connect)
- kurek_daemon.py ~L986-1031: Hermes, Muse and ledger blocks read inline in _handle_text_query_inner
- actions/memory_tool.py ~L43-90: recall reads the same files again
