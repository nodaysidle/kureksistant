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


@pytest.fixture(autouse=True)
def _isolated_xdg_dirs(tmp_path_factory, monkeypatch):
    """Redirect XDG data/state dirs so tests never touch ~/.local/share or ~/.local/state."""
    xdg_root = tmp_path_factory.mktemp("xdg")
    data_home = xdg_root / "data"
    state_home = xdg_root / "state"
    data_home.mkdir()
    state_home.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))


@pytest.fixture(autouse=True)
def _isolated_memory_db(tmp_path, monkeypatch):
    """No test may open the live ~/.local/share/kurek or repo memory/kurek_memory.db."""
    from memory import ledger

    monkeypatch.setattr(ledger, "DB_PATH", tmp_path / "kurek_memory.db")


@pytest.fixture(autouse=True)
def _isolated_claim_log(tmp_path, monkeypatch):
    """Spoken-claim violations go to a temp file, never ~/.local/share/kurek."""
    monkeypatch.setenv("KUREK_CLAIM_LOG", str(tmp_path / "claim-violations.jsonl"))


@pytest.fixture(autouse=True)
def _no_user_vocabulary(tmp_path, monkeypatch):
    """Transcript corrections must not depend on the owner's real reverseduo dictionary."""
    monkeypatch.setenv("KUREK_VOCAB_FILE", str(tmp_path / "no-dictionary.json"))


@pytest.fixture(autouse=True)
def _pooled_sessions_follow_requests_post(monkeypatch):
    """STT, web_search and the DeepSeek client post on module-level sessions; older tests fake
    the network by replacing requests.post, so route those sessions through it (looked up at call time)."""
    import requests
    from actions import web_search
    from core import llm_client, stt
    for mod in (stt, web_search, llm_client):
        # setitem on the instance dict: the undo deletes the key instead of leaving a bound method behind.
        monkeypatch.setitem(vars(mod._HTTP), "post", lambda *a, **k: requests.post(*a, **k))


@pytest.fixture(autouse=True)
def _isolated_runtime_dir(tmp_path_factory, monkeypatch):
    """XDG_RUNTIME_DIR is a private per-test temp dir, so no test can reach the live daemon's kurek.sock
    or its mpv (kurek_mpv.sock, e.g. through the real TTSPlayer.stop()), whatever env pytest starts from."""
    from core import mpv_sink

    run_dir = tmp_path_factory.mktemp("run")  # short path: AF_UNIX socket paths are limited to 108 bytes
    run_dir.chmod(0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run_dir))
    monkeypatch.setattr(mpv_sink, "_GLOBAL_SINK", None)  # the shared sink resolves its socket in this dir
    yield
    sink = mpv_sink._GLOBAL_SINK
    if isinstance(sink, mpv_sink.MpvPipeWireSink):
        sink.shutdown()  # an mpv this test started ends with it instead of piling up until exit
