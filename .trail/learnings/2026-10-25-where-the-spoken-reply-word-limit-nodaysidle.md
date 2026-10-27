---
title: Where the spoken-reply word limit lives
author: Nodaysidle
date: 2026-10-25
branch: chore/claude-canary
cost: { minutes: 1, tokens: 64778 }
touches:
  - CLAUDE.md
  - kurek_daemon.py
  - core/prompt.txt
  - scripts/rules_eval.toml
---

## Decided
- The spoken-reply word limit is prompt-only; no code truncates replies. It must match in 3 places: kurek_daemon.py (VOICE & SPEECH RULES rule 3), core/prompt.txt (rule 8 Extreme Brevity), and the user's ~/.config/kurek/rules.toml brevity rule. scripts/rules_eval.toml brevity cases (max_words) should track it.

## Tried and ruled out
- Looking for a hard word/token cap in core/ or actions/: none exists (llm_client max_tokens is 16000, unrelated).

## Watch out
- The same rule also says "1 to 2 short sentences"; raising only the word count may not lengthen replies, since the sentence limit binds first.
- rules_eval.py calls the paid LLM API; restart kurek.service after prompt changes.
