---
title: Promoting approved proposals into rules.toml
author: Nodaysidle
date: 2026-10-27
branch: chore/claude-canary
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
  - core/prompt.txt
---

## Decided
- Marking a proposal `approved` in ~/kurek_proposed_rules.md does nothing on its own. It reaches Kurek only once its text is copied verbatim into ~/.config/kurek/rules.toml as a [[rule]] block with a unique id and the text (`pin = true` is optional).
- Only behaviour rules go into rules.toml. Approved proposals that are facts (hardware, OS, terminal, usage) belong in ~/MEMORY.md, so each fact lives in one store.
- Raw "Heard (verbatim)" proposals that are not usable rules get skipped when another approved proposal already covers what they mean.
- Each added rule gets a `# proposal P-..., approved by Alan` comment above it.

## Tried and ruled out
- Restarting the daemon is not needed. kurek_daemon.py calls rules_prompt_block() inside the per-query handler, so the file is re-read on every query.

## Watch out
- Back up rules.toml before editing it: cp -p ~/.config/kurek/rules.toml ~/rules.toml.$(date +%F)
- Validate after an edit: .venv/bin/python -c "from core.rules import load_rules; print(len(load_rules().rules))". A duplicate id or bad TOML means no rules load at all; Kurek keeps running and puts a notice in the prompt saying no rules are loaded.
- ~/MEMORY.md is edited by hand only: suggest lines for it, don't write it.
- scripts/rules_eval.py makes paid model calls, so ask before running it.
