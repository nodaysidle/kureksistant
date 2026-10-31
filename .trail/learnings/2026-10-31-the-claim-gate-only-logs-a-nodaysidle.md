---
title: "The claim gate only logs: a spoken done is checked per sentence against this turn's tool outcomes"
author: Nodaysidle
date: 2026-10-31
branch: feat/english-only-claim-gate
cost: { minutes: 3, tokens: 25523 }
id: l-e8eb61ce
scope: repo
status: active
src: agent
touches:
  - core/claim_gate.py
  - kurek_daemon.py
  - scripts/rules_eval.py
---

## Decided
2026-11-01: the claim gate (core/claim_gate.py) is observe-only. It checks each sentence just before it is queued for speech, so it adds no latency and holds nothing back; a claim in a sentence already spoken cannot be caught. It never changes speech and a gate error falls through to normal speech. Rewrite and block modes wait for about a week of real data in ~/.local/share/kurek/claim-violations.jsonl.
- Tool outcomes are read from the tools' result text (no tool was changed). A delete is parked behind the on-screen confirmation, so "Deleted it" in the same turn is always unbacked (pending_confirmation).
- Only DELETED, SENT and CREATED are checked, English only. Hedge and negation words count only within 40 characters before the claim verb, so "Saved it, no problems" is still checked.

## Tried and ruled out
- Holding the whole reply until the end to validate it: unnecessary, the per-sentence hook is enough and keeps voice latency.
- A repair mode (second model call per violation): adds latency and can loop.
- Binding "run" or "manage_memory" to any action: a read or a recall then backed "I deleted it". Binding is by action now (exec, remember, write-type).
- The tier system and auto-written memory from the upgrade idea: conflicts with the rule that only the user writes rules and one fact lives in one store.

## Watch out
- scripts/rules_eval.py runs the real engine, so its replies reached the real log until it set KUREK_CLAIM_GATE=off. Tests use a temp log (conftest).
- "written" as an adjective ("a written message") was a false positive; it now counts only as a perfect tense.
- Outcome-tagged history, path binding for CREATED and rewrite/block are deferred on purpose: the first changes the prompt, the others need log data.
- `.trail/` is in the global git ignore here, so a new learning needs `git add -f`.
- test_uds_subscribe raced bind() and listen(); the test now retries the connect.

In the code: core/claim_gate.py (CLAIMS, evaluate, observe), kurek_daemon.py (_observe_claims, outcomes list in _handle_text_query_inner).
