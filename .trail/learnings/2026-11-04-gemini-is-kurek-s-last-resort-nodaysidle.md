---
title: "Gemini is Kurek's last-resort voice, and it cannot be told to sound like a wise older man"
author: Nodaysidle
date: 2026-11-04
branch: fix/voice-fallbacks
cost: { minutes: 10, tokens: 49288 }
id: l-71199df8
scope: repo
status: active
src: agent
touches:
  - actions/run.py
  - tests/test_remote_exec_gate.py
  - ".env.example"
  - README.md
  - actions/process_sentinel.py
  - core/tts.py
  - kurek_daemon.py
  - tests/test_tts_openrouter.py
---

## Decided
2026-11-04, owner: Gemini TTS with the user's own key is the last cloud voice, after xAI and OpenRouter, because a voice that reads the reply beats the stub. When everything fails Kurek stays silent rather than play spd-say's stock clip.

## Tried and ruled out
A style line in the prompt ("Say in the voice of a wise older man: ..."): Gemini reads it out loud, confirmed by transcribing the audio, and clips double in length. systemInstruction on the TTS models: HTTP 400. Age and tone can only come from the preset voice.

## Watch out
The free tier gave 429 after 3 requests a minute on one model, so synthesis rotates models; a long reply can still lose sentences. Daily limit unknown. Gemini returns ready WAV despite older docs describing raw PCM, so do not add a header. Phone playback of WAV is untested.

In the code: core/tts.py _synthesize_gemini, _GEMINI_TTS_MODELS, SystemSpeechEngine._spd_has_synthesizer; KUREK_GEMINI_VOICE (default Charon).
