"""Smoke test: daemon boots and GET /status returns idle without API keys."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DAEMON = ROOT / "kurek_daemon.py"
STATUS_URL = "http://127.0.0.1:8790/status"


def _wait_status(timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(STATUS_URL, timeout=1.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_err = exc
            time.sleep(0.25)
    raise AssertionError(f"Daemon /status not ready within {timeout}s: {last_err}")


def test_daemon_status_idle_without_api_keys(tmp_path: Path) -> None:
    assert DAEMON.is_file(), f"missing daemon entrypoint: {DAEMON}"

    env = os.environ.copy()
    # Ensure the smoke test does not pick up developer secrets if present.
    for key in (
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "DEEPGRAM_API_KEY",
        "TYPESAFE_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        env.pop(key, None)

    log_path = tmp_path / "daemon.log"
    with log_path.open("wb") as log_fh:
        proc = subprocess.Popen(
            [sys.executable, "-u", str(DAEMON)],
            cwd=str(ROOT),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            payload = _wait_status(timeout=90.0)
            assert payload.get("state") == "idle", payload
        finally:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
