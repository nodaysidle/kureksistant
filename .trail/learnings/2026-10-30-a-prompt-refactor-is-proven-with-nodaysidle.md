---
title: "A prompt refactor is proven with a before-and-after snapshot of the model's messages, not the rules eval"
author: Nodaysidle
date: 2026-10-30
branch: refactor/memory-context
cost: { minutes: 21, tokens: 126237 }
id: l-90c15fda
scope: repo
status: active
replaces: l-a9196e8b
src: agent
touches:
  - CHANGELOG.md
  - README.md
  - actions/memory_tool.py
  - core/context.py
  - kurek_daemon.py
  - memory/ledger.py
  - memory/reference.py
  - scripts/verify.sh
  - tests/conftest.py
---

## Decided
- 2026-10-30: the memory/prompt refactor was proven safe by snapshotting the exact messages sent to the model (and recall output) from one fake home, before and after, and requiring them to be byte-identical. That proves no prompt change at all, which is stronger than the rules eval (an LLM sample) and costs no API calls. Use the rules eval only when the prompt text really changes.
- The old memory manager was deleted: only its DB path was still used. Its legacy JSON import had already stopped running, and the README no longer claims it.

## Watch out
- The shared test helper that builds a light engine stubs Hermes to "no dirs". A snapshot or test that should cover Hermes has to put the real resolver back, or Hermes silently drops out of the prompt.
- On loopback, /remote/prompt still needs the Bearer token (only the UDS/CLI path is open). Read it from .env inside the command; never print it.
- Moving WHO_WINS into the context module kept the prompt identical because the persona embeds the same string at the same spot.

In the code:
- core/context.py build_messages, WHO_WINS; memory/reference.py prompt_block/entries/hermes_dir; memory/ledger.py DB_PATH
- tests/test_stream_errors.py _make_lite_engine patches reference.resolve_hermes_memory_dirs -> []
- snapshot recipe: freeze daemon.datetime, HOME=tmp, HERMES_MEMORIES_DIR=tmp/hermes, KUREK_AUTO_SCREEN=0, patch ledger.DB_PATH and core.rules.RULES_PATH, fake stream_deepseek_sentences, then cmp the JSON
