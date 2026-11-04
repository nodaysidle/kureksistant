---
title: "Kurek's shell habits are taught in the run tool's own description, and ~/Projects is flagged in every result"
author: Nodaysidle
date: 2026-11-02
branch: feat/english-only-claim-gate
cost: { minutes: 13, tokens: 35231 }
id: l-2deed4c2
scope: repo
status: active
replaces: l-5880d9d6
src: agent
touches:
  - kurek_daemon.py
  - actions/run.py
  - tests/test_remote_exec_gate.py
---

## Decided
2026-11-02, from Kurek's self-audit (K-2, K-7, K-8): the habits go into the `run` tool description, which the model reads at every call, not into rules.toml (long rules crowd out the language rule). A command or cwd under ~/Projects gets a note appended to its result. In unrestricted mode a missing cwd is refused instead of silently running in ~.
2026-11-02, owner: ~/Projects (old Mac copy, 144G) was deleted. The two live tools in it moved to ~/dev/nodaysidle/nds-tasks and ~/dev/nodaysidle/decide-mcp; ~/.local/bin links, opencode/cursor MCP configs, the decide skill and the vault's ask-decide were re-pointed. Backups of every edited config: ~/.local/state/projects-removal-2026-11-02/.

## Watch out
- Old docs, handoffs and learnings still name ~/Projects paths; they are history, not live config.
- All 10 run refusals in run_audit.log are from 2026-10-15, before KUREK_REMOTE_ALLOW_EXEC=1 was set. In unrestricted mode there are no allowlist walls, so "stop testing the walls" only matters in sandbox mode.
- The daemon runs tool calls one after another (kurek_daemon.py tool loop), never in parallel. Batching calls only saves model round trips.

In the code: actions/run.py `_HABITS`, `_LEGACY_PROJECTS_RE`, unrestricted cwd check in `_action_exec`; tests in tests/test_remote_exec_gate.py.
