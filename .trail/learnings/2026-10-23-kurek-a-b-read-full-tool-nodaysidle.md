---
title: "Kurek A/B: read full tool args, not the callback summary"
author: Nodaysidle
date: 2026-10-23
branch: ship/v0.2.2
cost: { minutes: 2, tokens: 10255 }
touches:
  - tests/test_browser_go_to_url.py
  - scripts/llm_ab.py
---

## Decided
- Supersedes the Watch out in "Kurek on Claude Haiku: provider switch and A/B result": Haiku did NOT drop the go_to url and `desktop/notes.txt` is valid (file_controller shortcut). Haiku made no tool-arg mistakes in that run.
- browser_control go_to with an empty url now returns an error instead of "Opened".

## Tried and ruled out
- Judging tool calls from the daemon's `{"type": "tool"}` callback: its description keeps only the first 2 args (`list(fn_args.items())[:2]`), so later args look missing.

## Watch out
- Use the `[Kurek Tool Dispatch]` log line or scripts/llm_ab.py's `tools` field (now full JSON args) before calling a tool call wrong.
