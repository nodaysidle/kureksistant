"""Ensure the repo root is importable for offline unit tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture(autouse=True)
def _no_desktop_toasts(tmp_path_factory, monkeypatch):
    """Failure-path tests call notify-send for real; shadow it so a test run is silent."""
    stub_dir = tmp_path_factory.getbasetemp() / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "notify-send"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}:{os.environ['PATH']}")
