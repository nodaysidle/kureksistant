---
title: "Voice names are fixed by the user's own replacements, not engine hints, and a typed box covers what still fails"
author: Nodaysidle
date: 2026-11-01
branch: feat/english-only-claim-gate
cost: { minutes: 16, tokens: 121817 }
id: l-8f2bbbe1
scope: repo
status: active
src: agent
touches:
  - core/vocabulary.py
  - kurek_daemon.py
---

## Decided
2026-11-01, owner: spoken names the microphone gets wrong are corrected from the replacements in reverseduo's ~/.config/reverseduo/dictionary.json (read in place, never copied or written), on every transcript. A one-line typed box (bin/kurek-ask, SUPER + SHIFT + K, floating Ghostty via xdg-terminal-exec) covers the rest; it uses `kurek prompt`, so a typed "yes" resolves a pending confirmation like a spoken one.
- Skipped on purpose: 139 of 279 replacements (single common words, Kurek command words, words of 3 letters or fewer, symbol words like "slash" -> "/", case-only changes, non-ASCII originals, other machines' paths).

## Tried and ruled out
- Keyword hints to the speech engines: the active engine is OpenRouter Whisper, whose prompt option is provider-specific, and Whisper prompts can echo their words from silence. Deepgram takes `keywords` but only runs when no OpenRouter key is set. The dictionary's `vocabulary` list is therefore unused.
- A GUI library for the box: the project forbids one. A floating terminal is enough.

## Watch out
- Hyprland here uses Lua config: `hyprctl dispatch closewindow address:..` fails. Close a test box by ending its script, with a pkill pattern that cannot match your own shell (e.g. "[.]local/bin/kurek-as[k]"); the window's process may be the shared terminal.
- Omarchy already binds SUPER+K, SUPER+ALT+K and SUPER+CTRL+K.
- Tests point KUREK_VOCAB_FILE at a missing temp path (conftest), so they never depend on the real dictionary.

In the code: core/vocabulary.py (_keep_replacement, correct), kurek_daemon.py (_process_recorded_audio), ~/.config/hypr/bindings.lua (bind and window rule, backup bindings.lua.bak-20261101-before-kurek-ask).
