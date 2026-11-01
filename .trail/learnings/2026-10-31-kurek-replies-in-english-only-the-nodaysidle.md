---
title: Kurek replies in English only; the language rule must be pinned and explicit
author: Nodaysidle
date: 2026-10-31
branch: feat/english-only-claim-gate
cost: { minutes: 1, tokens: 1770 }
id: l-0818ea6f
scope: repo
status: active
replaces: l-1bb80b
src: agent
touches:
  - core/auto_recall.py
  - scripts/rules_eval.toml
---

## Decided
2026-10-31, owner: Kurek speaks and is spoken to in English only. The old "English, Slovenian or Italian, reply in his language" policy (2026-10-28) is replaced. The language rule is `english-only` in ~/.config/kurek/rules.toml (still the only place a language policy may live, see tests/test_anti_drift_prompts.py). Recall and the claim gate are English-only too.

## Tried and ruled out
- An unpinned, plain "always reply in English" rule: a Slovenian greeting still got a Slovenian reply, 0/2 on the eval. The model mirrors the message language, helped by ~/MEMORY.md listing the location as Slovenia.
- What fixed it: `pin = true` plus wording that names the cases ("even when a message is in Slovenian, Italian or any other language, and even though he lives in Slovenia"). 3/3, and 84/84 on the full eval.

## Watch out
- Removing the Slovenian/Italian filler list from core/auto_recall.py also removed the English words "in" and "per", which then counted as subject words and made recall match nearly every past turn. They are back in `_NOT_SUBJECT`, with a test. Check English words before deleting any word list.
- rules_eval case `slovenian-allowed` was inverted into `slovenian-gets-english`; `italian-gets-english` was added.
- The old rule id `english-Slovenian-Italian-only` no longer exists; the backup is ~/.config/kurek/rules.toml.bak-2026-10-31-before-english-only.

In the code: core/auto_recall.py (_NOT_SUBJECT), scripts/rules_eval.toml (language cases), ~/.config/kurek/rules.toml (english-only, pinned).
