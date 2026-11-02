---
title: "Kurek's shell habits are taught in the run tool's own description, and ~/Projects is flagged in every result"
author: Nodaysidle
date: 2026-11-02
branch: feat/english-only-claim-gate
cost: { minutes: 11, tokens: 139678 }
id: l-5880d9d6
scope: repo
status: active
src: agent
touches:
  - actions/run.py
  - kurek_daemon.py
  - tests/test_remote_exec_gate.py
  - actions/file_controller.py
---

## Decided
2026-11-02, from Kurek's self-audit (K-2, K-7, K-8): the habits go into the `run` tool description, which the model reads at every call, not into rules.toml (long rules crowd out the language rule). A command or cwd under ~/Projects gets a note appended to its result. In unrestricted mode a missing cwd is refused instead of silently running in ~.

## Watch out
- ~/Projects still exists on disk (old Mac copy, ~300 entries, 144G, with git repos such as nodaysidle-clipgenius). Kurek ran git fetch/diff there on 2026-10-22 thinking it was live. Moving it is the owner's call.
- All 10 run refusals in run_audit.log are from 2026-10-15, before KUREK_REMOTE_ALLOW_EXEC=1 was set. In unrestricted mode there are no allowlist walls, so "stop testing the walls" only matters in sandbox mode.
- The daemon runs tool calls one after another (kurek_daemon.py tool loop), never in parallel. Batching calls only saves model round trips.

In the code: actions/run.py `_HABITS`, `_LEGACY_PROJECTS_RE`, unrestricted cwd check in `_action_exec`; tests in tests/test_remote_exec_gate.py.
