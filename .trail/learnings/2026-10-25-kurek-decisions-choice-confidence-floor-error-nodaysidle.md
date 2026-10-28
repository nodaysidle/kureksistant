---
title: "Kurek decisions: choice confidence floor, error-judgment cache, redaction gaps"
author: Nodaysidle
date: 2026-10-25
branch: chore/claude-canary
cost: { minutes: 8, tokens: 35903 }
touches:
  - ".env.example"
  - AGENTS.md
  - CLAUDE.md
  - README.md
  - actions/code_helper.py
  - actions/dev_agent.py
  - actions/run.py
  - core/decision_client.py
  - kurek_daemon.py
  - memory/config_manager.py
---

## Decided
- choice answers must carry a finite `confidence` in [0,1] (else provider failure → next provider). Below CHOICE_CONFIDENCE_FLOOR (0.8) the answer is valid but not acted on: caller's local fallback decides. Stops a low-confidence `none` from passing a broken dev-agent build.
- dev_agent caches only real model answers in _ERROR_JUDGMENTS, keyed by the redacted 4000-char tail, cleared at 16. Fallback results are never cached, so an outage is retried. Supersedes the earlier lru_cache note: tests clear _ERROR_JUDGMENTS, there is no cache_clear().
- query_openrouter_needs_screen and parse_needs_screen_noul deleted (test-only, bypassed the fallback chain).
- actions/run._redact_secrets: URL credentials for any scheme (postgres, redis, mongodb+srv), and case-sensitive ENV_STYLE_KEY= values of 8+ chars.
## Watch out
- If live Jev choice answers omit `confidence`, every intent/error decision drops to Decision-1 then the local fallback. Check with one real call.
