---
title: "A wordy simple answer came from chat history and a garbled word, so the eval needs seeded history to measure it"
author: Nodaysidle
date: 2026-11-01
branch: feat/english-only-claim-gate
cost: { minutes: 33, tokens: 25037 }
id: l-637de624
scope: repo
status: active
src: agent
touches:
  - scripts/rules_eval.py
  - scripts/rules_eval.toml
---

## Decided
2026-11-01: a simple fact question ("which terminal?") got a reply reading out "entry #27, set by you", an old Velocity conflict and "Fuses didn't parse". Fixed by a `memory-talk` rule in ~/.config/kurek/rules.toml (unpinned): 2/5 before, 5/5 after, other recall and memory cases unchanged.

## Tried and ruled out
- The plain prompt "Which terminal do I use?" in a fresh eval engine: passes without the rule, so it cannot show the problem. It needs earlier turns plus the garbled word from the live voice input.

## Watch out
- rules_eval cases can now carry `history` (earlier turns). Use it for any behavior that only shows with a chat behind it.
- `ask-instead-of-guessing` is flaky (3/5 to 5/5 across runs), unrelated to this rule.

In the code: scripts/rules_eval.py (history), scripts/rules_eval.toml (memory-talk-stays-quiet).
