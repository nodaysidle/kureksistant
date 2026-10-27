---
title: "Kurek CI gate: ruff F401 after decision-client move; hermetic decision tests"
author: Nodaysidle
date: 2026-10-22
branch: ship/v0.2.2
cost: { minutes: 2, tokens: 69815 }
touches:
  - AGENTS.md
  - core/prompt.txt
  - kurek_daemon.py
  - tests/test_decision_screen_context.py
  - core/decision_client.py
  - bin/kurek-trigger.c
  - config/api_keys.json
---

## Decided
- After f36cbd7 moved TypeSafe key lookup into core/decision_client.py, kurek_daemon.py's `get_typesafe_key` import went unused and failed `ruff check .` (F401 is enforced in CI). Removed it.
- Tests that patched `daemon.get_typesafe_key` were no-ops; removed. test_no_keys_skips_network now patches config_manager.get_mouse_key / get_openrouter_api_key / get_typesafe_key to None, because decision_client reads keys from config_manager, not the daemon module.

## Tried and ruled out
- Keeping the daemon import just so monkeypatch.setattr(daemon, ...) keeps working: it hides dead patches and keeps ruff red.

## Watch out
- Run tests with .venv/bin/python (system python has no pytest). Full CI gate = ruff, gcc -Werror on bin/kurek-trigger.c, shellcheck (not installed locally), package_release.sh dry run, xvfb pytest.
- Decision-provider tests must patch keys on memory.config_manager; the owner's real keys live in config/api_keys.json and will leak into tests otherwise.
