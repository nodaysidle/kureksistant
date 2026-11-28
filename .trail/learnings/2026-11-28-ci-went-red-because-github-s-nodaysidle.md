---
title: "CI went red because GitHub's ubuntu-latest moved to 26.04, whose PortAudio fails at import without an audio server"
author: Nodaysidle
date: 2026-11-28
branch: main
cost: { minutes: 75, tokens: 938115 }
id: l-3f8c86af
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/ci.yml"
  - ".env.example"
  - HANDOFF.md
  - kurek_daemon.py
  - core/tts.py
  - docs/ANTI-DRIFT.md
  - core/context.py
  - memory/ledger.py
  - actions/open_app.py
  - actions/computer_control.py
---

## Decided
2026-11-28: pin the CI job to ubuntu-24.04. Nothing in the repo caused the failure: the last green run (2026-11-04) was on the ubuntu-24.04 image, the first red one (2026-11-22, the v0.2.5 release commit) on ubuntu-26.04. On 26.04 libportaudio2 is built with a PulseAudio backend; with no PulseAudio server, Pa_Initialize fails, and sounddevice calls it at import. 40 test modules import the daemon or the TTS module at the top, so collection stops with 40 identical errors.
2026-11-28: every hyprctl dispatch in Kurek goes through one helper that sends the Lua form first, reads the reply text, and falls back to the old form for non-Lua Hyprland. The exit code is never trusted.
2026-11-28, owner: (1) The shell tool refuses any command that names rules.toml unless it is plainly read-only. On 2026-11-08, asked to change how it addresses the owner, the model ran a python heredoc through the shell tool and rewrote the rules file; the file tool refused that file, the shell tool had no check. (2) Kurek reads only Hermes USER.md. Hermes MEMORY.md is Hermes's own working memory: it carried a managed block of sprint-team instructions in which "Boris" is a Hermes agent, and Kurek's model received it every turn. (3) The legacy ~/ALIGNMENT_SYNTHESIS.md is no longer read at all.

## Tried and ruled out
- Lazy sounddevice import in the daemon: the daemon imports sounddevice before numpy on purpose (import deadlock), so the order must stay.
- A conftest stub for sounddevice: it would also replace the real module in local runs.
- An environment variable to pick a dummy backend: sounddevice reads none (only SD_ENABLE_ASIO).
- Starting PulseAudio in the job: possible, but every test gets a private XDG_RUNTIME_DIR, so PULSE_SERVER would have to be pinned too. Untested.
- Checking the exit code: `hyprctl dispatch focuswindow address:0x0` on Hyprland 0.57.2 with a Lua config prints `error: ... ')' expected near 'address'` and exits 0.
- Keeping only the old syntax: the public repo still has users on a non-Lua Hyprland, so the old form stays as the fallback, the same order Omarchy's own omarchy-launch-or-focus uses.
- Filtering only the managed block out of Hermes MEMORY.md: the rest of that file is also Hermes's operating rules, and a future managed block would leak again. Dropping the file is the fix.
- Importing the rules path into the shell tool: the anti-drift test forbids RULES_PATH in any action module, so the guard matches the file name in the command text instead.
- rg and less in the read-only command set: rg --pre runs a program, less -o writes a file.

## Watch out
- Collection has been failing since 2026-11-22, so nothing after that date was ever tested on CI until the pin ran.
- Arch's portaudio has only ALSA and JACK, so the owner's machine is unaffected. If Arch ships the PulseAudio backend, the daemon would fail at import whenever pipewire-pulse is down.
- When 24.04 is retired: start pulseaudio in the job and set PULSE_SERVER.

In the code: .github/workflows/ci.yml (runs-on), kurek_daemon.py (sounddevice import near the top), core/tts.py.
- A reply starting with `error` means the dispatch was not parsed; `warning: ... not found` means it parsed but did nothing. Both are "not done".
- Never test a dispatcher on the owner's live session unless it targets the nonexistent window address:0x0: workspace, close, fullscreen and float dispatches change what he is looking at.
- The Lua form is `hl.dsp.<name>({...})`, e.g. `hl.dsp.focus({window="address:0x..."})`; take names from Omarchy's scripts, which are in daily use here.

In the code: actions/open_app.py, actions/computer_control.py, actions/computer_settings.py, tests/test_hypr_dispatch.py, tests/test_gate_fixes_v021.py.
- The guard does not catch an indirect write (a script that writes the file, a glob such as r*). If it happens again, make the file read-only at the filesystem level.
- A ledger correction whose source is not manage_memory, daily/ or jev counts as made outside the chat: chat turns from before it stop being sent to the model. That is wanted after a correction, and it is why the owner's corrections of 2026-11-28 use the source the owner's correction made outside the chat.
- The owner's form of address is "Maestro Boris". It lives in the rules file and in one ledger row; both must be changed together.
- The owner's personal eval cases (~/.config/kurek/rules_eval.toml) assert his facts (terminal, city, name). Update them with the facts or the eval reports false failures.

In the code: actions/run.py (_rules_file_refusal), tests/test_run_rules_guard.py, memory/reference.py, core/context.py (WHO_WINS), docs/ANTI-DRIFT.md, scripts/rules_eval.py.
