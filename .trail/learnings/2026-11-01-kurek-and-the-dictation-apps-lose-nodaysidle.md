---
title: Kurek and the dictation apps lose the mic when another app holds the USB device directly
author: Nodaysidle
date: 2026-11-01
branch: feat/english-only-claim-gate
cost: { minutes: 5, tokens: 43642 }
id: l-4d9ccb2f
scope: repo
status: active
src: agent
touches:
  - kurek_daemon.py
---

## Decided
2026-11-01: Kurek logging `Error opening InputStream ... PaErrorCode -9999 host API not found` on every click (listen starts, then drops to idle) means the microphone is gone from PipeWire, not that the button or daemon is broken. Here the `hex` dictation service held the USB mic (Trust GXT 232, hw:0) through ALSA directly, so PipeWire logged "capture open failed: Device or resource busy" and had no `alsa_input` source. The owner removed hex.
- Fix order: find the holder with `fuser -v /dev/snd/pcmC0D0c`, stop it, then `systemctl --user restart wireplumber` so PipeWire re-detects the device (`pactl list sources short | grep alsa_input` must show it), then `systemctl --user restart kurek` because PortAudio keeps a stale device list.

## Tried and ruled out
- Blaming the toggle or button: the daemon showed LISTENING then the mic error then IDLE each time.
- Cancelling a hex dictation to release the device: `hex dictation cancel` did not free it; hex kept the device while its service was ready.

## Watch out
- reverseduo reads DEEPGRAM/ANTHROPIC/OPENROUTER keys from its environment. Relaunched from a Claude Code shell it loses ANTHROPIC_API_KEY and records without finishing; relaunch with the keys taken from `systemctl --user show-environment`.
- Never `pkill -f` with a pattern that appears in your own command line.
- Removed hex files are in ~/hex-removed-2026-11-01 (bindings backup: ~/.config/hypr/bindings.lua.bak-20261101-before-hex-removal).

In the code: kurek_daemon.py (mic open in the listen path).
