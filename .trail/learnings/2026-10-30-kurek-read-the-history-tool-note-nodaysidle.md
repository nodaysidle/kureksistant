---
title: Kurek read the history tool note aloud; the reply log hides it
author: Nodaysidle
date: 2026-10-30
branch: fix/history-tool-record
cost: { minutes: 2, tokens: 91681 }
id: l-679e139a
scope: repo
status: active
src: agent
touches:
  - kurek_daemon.py
  - tests/test_history_tools.py
  - core/live_voice.py
---

## Decided
2026-10-30: the daemon's "(Tool calls in that turn, recorded by the daemon: ...)" note is added only to assistant turns that really ran tools. With a "none" note on every turn the model learned to copy it, and Kurek spoke it ("verbosely says tool call").
Speech also stops at the first streamed sentence that contains the note.

## Watch out
The note is cleaned out of reply_text only after the sentences were already sent to text-to-speech and the phone. The `[Kurek Reply]` log line prints the cleaned text, so the log never shows what was actually spoken. Anything the model copies from the prompt must be filtered in the sentence path, not just in the final clean-up.

In the code: kurek_daemon.py history_message and the `note_copied` flag in the "sentence" branch; tests/test_history_tools.py.
