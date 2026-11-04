---
title: "When xAI runs out of credits, Kurek speaks the same Grok voices through OpenRouter; live voice stays off"
author: Nodaysidle
date: 2026-11-01
branch: feat/english-only-claim-gate
cost: { minutes: 18, tokens: 142530 }
id: l-ac3ae6b7
scope: repo
status: active
src: agent
touches:
  - core/tts.py
  - ".env.example"
---

## Decided
2026-11-01, owner: xAI credits ran out (HTTP 403 "used all available credits"), so every sentence fell back to robotic spd-say. OpenRouter serves the same Grok voices (x-ai/grok-voice-tts-1.0, voices eve/ara/rex/sal/leo) on /api/v1/audio/speech at $15 per 1M chars. The TTS engine now tries xAI direct, and on 401/402/403 switches to OpenRouter until the daemon restarts. Owner picked voice Leo (same price as Sol). Live voice on the phone is off until xAI is recharged: it needs xAI's speech-to-speech service directly and has no OpenRouter route.

## Tried and ruled out
Gemini 3.8 Flash TTS on OpenRouter: it only returns raw PCM, the player expects mp3, so it needs extra plumbing. ElevenLabs v3 Conversational (george) sounded most human but costs ~2.7x ($40/1M chars); owner chose value.

## Watch out
After recharging xAI, restart the daemon to go back to xAI direct, and set KUREK_LIVE_VOICE=1. Owner usage measured at ~225 chars per reply, ~49 replies a day: $10 lasts about two months on Grok voices.

In the code: core/tts.py XAITTSEngine.synthesize / _synthesize_openrouter; KUREK_TTS_VOICE (both routes), KUREK_TTS_MODEL (OpenRouter only); .env KUREK_LIVE_VOICE=0.
