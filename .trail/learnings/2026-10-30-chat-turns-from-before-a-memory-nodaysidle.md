---
title: Chat turns from before a memory correction made outside the chat are no longer sent to the model
author: Nodaysidle
date: 2026-10-30
branch: fix/history-tool-record
cost: { minutes: 16, tokens: 126613 }
id: l-dca3e734
scope: repo
status: active
replaces: l-2c256bf4
src: agent
touches:
  - GLOSSARY.md
  - README.md
  - memory/ledger.py
  - kurek_daemon.py
  - tests/test_history_tools.py
  - tests/test_ledger.py
---

## Decided
2026-10-30: prompt wording cannot make learned memory beat the user's own words in recent chat (0/4 earlier), so the daemon enforces it in code. Every chat turn now carries a timestamp. If a ledger correction comes from outside the chat (its source isn't manage_memory, daily/ or jev, e.g. a fix by hand or by Claude Code), turns stamped before it are left out of the messages, and the log warns once per cut-off. Corrections made inside the chat don't trigger it, because the chat already agrees with them. Live check on DeepSeek-Flash: 0/4 without the change, 3/4 with it (the fourth also answered correctly).

## Tried and ruled out
Clearing memory/kurek_history.json by hand after each outside correction (the old fix): it worked, but it relied on remembering to do it.

## Watch out
- Turns without a timestamp (written before 2026-10-30) count as older than any outside correction, so they are never sent while one exists.
- A new writer of ledger rows that runs during a chat must use a source starting with one of the chat prefixes, or every write will hide the chat.

In the code: memory/ledger.py outside_correction_ts + _CHAT_SOURCES, kurek_daemon.py history filter before messages are built, tests/test_history_tools.py, tests/test_ledger.py.
