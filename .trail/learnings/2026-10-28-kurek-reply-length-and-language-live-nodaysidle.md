---
title: "Kurek: reply length and language live only in rules.toml"
author: Nodaysidle
date: 2026-10-28
branch: main
cost: { minutes: 18, tokens: 67047 }
touches:
  - CHANGELOG.md
  - core/prompt.txt
  - docs/ANTI-DRIFT.md
  - kurek_daemon.py
  - scripts/rules_eval.toml
  - tests/test_anti_drift_prompts.py
  - HANDOFF.md
  - core/rules.py
---

## Decided
- 2026-10-28, owner: the anti-drift rules are non-negotiable. Reply length (brevity) and reply language live ONLY in ~/.config/kurek/rules.toml. The daemon system prompt says "follow the brevity and language rules in [USER RULES]" with a no-number fallback for installs without rules.
- Supersedes the Oct 25 learning "word limit must match in 3 places": core/prompt.txt was deleted (nothing loaded it) and the daemon prompt no longer states a number.
- Owner's limits (2026-10-28): up to 15 sentences / 300 words for explanations, opinions, debates; facts short. Conversation in English, Slovenian or Italian (reply in his language), any other input language gets English, technical work strictly English.
- Kurek saves files under ~/Downloads/KUREK/{IDEAS,PHOTO,NOTES} and picks the folder itself (rule kurek-folders).

## Watch out
- tests/test_anti_drift_prompts.py fails if a sentence/word count or a language policy reappears in the sent system prompt, if core/prompt.txt comes back, or if code other than core/rules.py references RULES_PATH. Change limits in rules.toml (owner's file, back it up, his wording), never in code.
- KUREK_STT_LANGUAGE=en in the owner's .env pins speech recognition to English, which garbles Slovenian/Italian speech. Owner decision pending.
- Proposal P-2026-10-24-05 ("for the lobby" clarifying question) is still pending by owner choice.
