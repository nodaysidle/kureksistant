---
title: "Kurek's memory near the limit was kernel file cache from his shell searches, so shell commands now run in their own scope"
author: Nodaysidle
date: 2026-11-02
branch: feat/english-only-claim-gate
cost: { minutes: 32, tokens: 78826 }
id: l-7894c89b
scope: repo
status: active
src: agent
touches:
  - actions/run.py
  - tests/test_remote_exec_gate.py
  - actions/code_helper.py
---

## Decided
2026-11-02: kurek.service showed 506M of MemoryHigh=512M, but the daemon itself used ~66M and mpv ~69M. 339M was reclaimable kernel slab (dentry/inode cache) from home-wide `find`/`rg` commands Kurek ran through the unrestricted shell, billed to the service cgroup; memory.events showed 538 `high` throttle events, 0 oom. Unrestricted shell commands now start under `systemd-run --user --scope`, the same wrapper dev_agent uses, so that cache is billed to a throwaway scope. Verified live: a 2M-file `find ~` left the service at 127M (slab under 1M).

## Tried and ruled out
Trimming the daemon itself (faster-whisper, imports): not needed, it is small. Raising MemoryHigh: hides the cause.

## Watch out
- memory.current counts kernel cache, not just the process. Read anon vs slab in memory.stat before blaming code.
- `echo <size> > memory.reclaim` on the service cgroup (user-writable) frees it on the spot, but it also pushes anon pages to swap.
- The sandboxed (non-unrestricted) run path is not wrapped; it only allows small read-only commands.

In the code: actions/run.py `_spawn_unrestricted_shell` uses actions.dev_agent `_wrap_systemd_scope` (lazy import, dev_agent imports run); test in tests/test_remote_exec_gate.py.
