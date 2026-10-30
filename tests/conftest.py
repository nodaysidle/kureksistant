"""Ensure the repo root is importable for offline unit tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


_LIVE_ENV = (
    "DEEPSEEK_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY",
    "DEEPGRAM_API_KEY", "TYPESAFE_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_kurek_env(monkeypatch):
    """A shell that exports the live .env (KUREK_REMOTE_ALLOW_EXEC, keys)
    must not change test outcomes; tests opt in with monkeypatch.setenv."""
    for name in list(os.environ):
        if name.startswith("KUREK_") or name in _LIVE_ENV:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_desktop_toasts(tmp_path_factory, monkeypatch):
    """Failure-path tests call notify-send for real; shadow it so a test run is silent."""
    stub_dir = tmp_path_factory.getbasetemp() / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "notify-send"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}:{os.environ['PATH']}")
