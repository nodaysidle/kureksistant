---
title: "AGENTS.md: human rules must live ABOVE the trail block"
author: Nodaysidle
date: 2026-10-25
branch: chore/claude-canary
touches:
  - CLAUDE.md
  - AGENTS.md
---

## Decided
- In this ecosystem, hand-written AGENTS.md content goes ABOVE the `<!-- trail:start -->` marker. trail build regenerates everything between the markers.
- Correct examples: nodaysidle-prompt-optimizer/AGENTS.md (human lines 1-10, block at 11) and kurekizmo/AGENTS.md (human content, block at 210).
- The ROOT /home/arch/dev/nodaysidle/AGENTS.md is the anomaly: its rules 1-6 sit INSIDE the block (lines 1-57) and will be wiped on next `trail build`. Fix = relocate rules above line 1.

## Tried and ruled out
- Looked for a trail config/template that owns the rules (.trail/config.*, cache grep for "unlikely edge cases"): none. The rules are hand-pasted, not generated — confirming they're at risk.

## Watch out
- The tree root is NOT a git repo, so edits to root AGENTS.md have no undo — back up before editing.
- CLAUDE.md promises "See AGENTS.md for delegation and workflow" but root AGENTS.md has neither; either add those sections or fix the pointer.
- Rule 5 (UI label text) is UI-specific and doesn't generalize to the FastAPI/Android/F1 sub-projects.
