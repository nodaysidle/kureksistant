---
title: "The strange Kurek voice came from main missing the OpenRouter voice commit, and the local fallback has no real synthesizer"
author: Nodaysidle
date: 2026-11-04
branch: main
cost: { minutes: 8, tokens: 177114 }
id: l-ab78f2f4
scope: repo
status: active
src: agent
touches:
  - actions/file_processor.py
  - tests/test_actions_hardening.py
  - ".env.example"
  - AGENTS.md
  - CHANGELOG.md
  - README.md
  - bin/kurek-ask
  - core/auto_recall.py
  - core/claim_gate.py
  - core/confirm.py
---

## Decided
2026-11-04: Kurek spoke every sentence as a canned speech-dispatcher sample. The OpenRouter voice commit stayed on the feature branch: the merge to main (ce3dad4) did not include it, and the daemon was restarted on main while xAI was still out of credits. Restored on main's working tree with a no-commit cherry-pick; the daemon then spoke through OpenRouter.

## Watch out
The local fallback is not a working voice here: no espeak or festival package was found, so spd-say runs the sd_dummy module, which plays dummy-message.wav instead of the text, about 30 s per sentence. If OpenRouter fails too, the strange voice returns. The daemon runs whatever branch the checkout is on, so a branch switch plus restart changes behaviour silently.

In the code: core/tts.py SystemSpeechEngine; afd2a5a; also on that branch and not on main: actions/run.py fixes d058d19, d5e2e38.
