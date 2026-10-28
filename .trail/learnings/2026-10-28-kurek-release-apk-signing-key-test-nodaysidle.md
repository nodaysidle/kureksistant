---
title: "Kurek release: APK signing key, test env leak, packaging from HEAD"
author: Nodaysidle
date: 2026-10-28
branch: main
cost: { minutes: 19, tokens: 449002 }
touches:
  - AGENTS.md
  - CHANGELOG.md
  - README.md
  - android/app/build.gradle
  - tests/test_web_search_deepseek.py
  - ".env.example"
  - CLAUDE.md
  - tests/conftest.py
  - core/prompt.txt
  - kurek_daemon.py
---

## Decided
- v0.2.2 (2026-10-28) ships the Android app signed with android/app/debug.keystore (gitignored, created 2026-10-12, cert SHA-256 436d46b2…7613). That is now the stable key: keep a backup, or the next release forces every user to uninstall again. The v0.2.1 release APK used a different key (42a962f9…4034), so v0.2.1 users must uninstall once.
- Bump android/app/build.gradle versionCode/versionName every release so a downloaded APK installs as an upgrade.
- Release flow: PR to main (CI must pass) -> tag on the merge commit -> `VERSION=x.y.z OUT_DIR=<dir> bash scripts/package_release.sh` (it git-archives HEAD, so commit first) -> assembleDebug -> append the APK to SHA256SUMS.txt -> `gh release create --verify-tag --latest`.
- README install snippet verifies only against SHA256SUMS.txt; a hard-coded tarball hash in README is circular (the README is inside the tarball).

## Tried and ruled out
- Blaming the code for ~140 local test failures: the owner's shell exports the whole live .env (KUREK_LLM=haiku, KUREK_REMOTE_ALLOW_EXEC=1, API keys). tests/conftest.py now clears KUREK_* and the API key vars per test.

## Watch out
- `env` on this machine is a uv shim (~/.local/bin/env); use `printenv` to inspect exported variables.
- No adb device was connected at release time, so the 0.2.2 APK was built and verified (aapt2/apksigner) but not click-tested on a phone by the agent.
