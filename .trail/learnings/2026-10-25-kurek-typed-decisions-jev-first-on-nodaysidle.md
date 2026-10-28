---
title: "Kurek typed decisions: Jev-first on OpenRouter Decisions"
author: Nodaysidle
date: 2026-10-25
branch: chore/claude-canary
cost: { minutes: 7, tokens: 148273 }
touches:
  - ".env.example"
  - AGENTS.md
  - CLAUDE.md
  - README.md
  - actions/code_helper.py
  - actions/dev_agent.py
  - core/decision_client.py
  - kurek_daemon.py
  - memory/config_manager.py
  - tests/test_decision_providers.py
---

## Decided
- core/decision_client.py order is jev (typesafe/jev-1.14) → decision1 (microsoft/microsoft-decision-1), both on https://openrouter.ai/api/alpha/decisions with OPENROUTER_API_KEY. One 1.5 s timeout per request (KUREK_DECISION_TIMEOUT).
- query_decision validates every sent question (noul finite in [0,1], choice in sent labels); invalid/missing answer = provider failure → next provider → caller's local fallback. `score` is rejected until a call site needs it.
- code_helper._detect_intent has no Gemini fallback anymore; structural fallback only.
- dev_agent._classify_error: Python markers (no module named, SyntaxError, Traceback) stay deterministic; only a bare error word without traceback goes to Jev via classify_error, redacted with actions.run._redact_secrets and capped to the last 4000 chars. Gated by KUREK_DECISION_INTENT. lru_cached so _has_error + the build loop make one call per output.
## Tried and ruled out
- Keeping Mouse-1 and direct TypeSafe (api.typesafe.ai) in the chain: dropped; get_mouse_key removed.
## Watch out
- Memory triage (_consolidate_with_jev in kurek_daemon.py) still uses typesafe_sdk directly with TYPESAFE_API_KEY; not migrated (ANTI-DRIFT scope).
- Tests that call dev_agent._classify_error must cache_clear() it.
