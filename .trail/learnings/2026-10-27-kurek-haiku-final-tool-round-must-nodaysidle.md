---
title: "Kurek Haiku: final tool round must keep tools; use tool_choice none"
author: Nodaysidle
date: 2026-10-27
branch: chore/claude-canary
cost: { minutes: 8, tokens: 175693 }
touches:
  - ".env.example"
  - AGENTS.md
  - CLAUDE.md
  - README.md
  - actions/code_helper.py
  - actions/dev_agent.py
  - actions/run.py
  - actions/web_search.py
  - core/decision_client.py
  - core/llm_client.py
---

## Decided
Haiku 5.5 thinking blocks replayed in a tool loop are bound to the tools list. To end a tool loop, send the same tools with tool_choice none (threaded through stream_deepseek_sentences → stream_deepseek → stream_haiku). Never pass tools=None after tool rounds.
## Tried and ruled out
Dropping tools on the last round (old behavior): 400 "Invalid signature in thinking block ... tools list differs".
## Watch out
The full pytest run fails ~156 tests if KUREK_LLM=haiku is exported in the shell. Run with env -u KUREK_LLM.
