"""Offline tests for OpenRouter Decision-1 auto screen-context gating."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from urllib.error import URLError

import pytest

import core.decision_client as decision_client
from core.decision_client import (
    NEEDS_SCREEN_INSTRUCTIONS,
    parse_needs_screen_noul,
    query_openrouter_needs_screen,
)

# Deliberately not an sk-/xai-/AIza-shaped token — packaging deny-list scans tests.
SECRET_KEY = "or-test-secret-DO-NOT-LEAK-9f82d28"


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")
        self.headers = {"Content-Type": "application/json", "X-Secret": SECRET_KEY}

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _noul_payload(noul: float | str | None, *, wrap: bool = True) -> dict:
    if not wrap:
        return {"answers": {"needs_screen": noul}}
    return {"answers": {"needs_screen": {"noul": noul}}}


def test_parse_needs_screen_noul_valid() -> None:
    assert parse_needs_screen_noul(_noul_payload(0.9)) == pytest.approx(0.9)
    assert parse_needs_screen_noul(_noul_payload(0.0)) == pytest.approx(0.0)
    assert parse_needs_screen_noul(_noul_payload(1)) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"answers": {}},
        {"answers": {"needs_screen": {}}},
        {"answers": {"needs_screen": {"noul": "high"}}},
        {"answers": {"needs_screen": {"noul": True}}},
        {"answers": {"needs_screen": {"noul": -0.1}}},
        {"answers": {"needs_screen": {"noul": 1.01}}},
        {"answers": {"needs_screen": "nope"}},
    ],
)
def test_parse_needs_screen_noul_invalid(payload) -> None:
    assert parse_needs_screen_noul(payload) is None


def test_query_openrouter_success(monkeypatch) -> None:
    calls: list[object] = []

    def fake_urlopen(req, timeout=None):
        calls.append((req, timeout))
        body = json.loads(req.data.decode("utf-8"))
        assert body["model"] == "microsoft/microsoft-decision-1"
        instr = body["questions"]["needs_screen"]["instructions"]
        assert instr == NEEDS_SCREEN_INSTRUCTIONS
        assert req.get_header("Authorization") == f"Bearer {SECRET_KEY}"
        return _FakeHTTPResponse(_noul_payload(0.9))

    result = query_openrouter_needs_screen(
        "what error is on my screen?",
        SECRET_KEY,
        timeout=1.5,
        urlopen=fake_urlopen,
    )
    assert result == pytest.approx(0.9)
    assert len(calls) == 1
    assert calls[0][1] == 1.5


def test_query_openrouter_malformed_returns_none() -> None:
    def fake_urlopen(req, timeout=None):
        return _FakeHTTPResponse({"answers": {"needs_screen": {"noul": "nope"}}})

    assert (
        query_openrouter_needs_screen("hi", SECRET_KEY, urlopen=fake_urlopen) is None
    )


def test_query_openrouter_timeout(capsys) -> None:
    def boom(_req, timeout=None):
        raise TimeoutError("timed out")

    assert query_openrouter_needs_screen("hi", SECRET_KEY, urlopen=boom) is None
    out = capsys.readouterr().out
    assert "TimeoutError" in out
    assert SECRET_KEY not in out
    assert "timed out" in out


def test_query_openrouter_exception_redacts_key(capsys) -> None:
    def boom(_req, timeout=None):
        raise URLError(f"auth failed for {SECRET_KEY}")

    assert query_openrouter_needs_screen("hi", SECRET_KEY, urlopen=boom) is None
    out = capsys.readouterr().out
    assert SECRET_KEY not in out
    assert "[REDACTED]" in out
    assert "URLError" in out


def test_auto_screen_opt_out(monkeypatch) -> None:
    monkeypatch.setenv("KUREK_AUTO_SCREEN", "0")
    assert decision_client.auto_screen_enabled() is False
    monkeypatch.setenv("KUREK_AUTO_SCREEN", "false")
    assert decision_client.auto_screen_enabled() is False
    monkeypatch.delenv("KUREK_AUTO_SCREEN", raising=False)
    assert decision_client.auto_screen_enabled() is True


def test_decision_timeout_configurable(monkeypatch) -> None:
    monkeypatch.delenv("KUREK_DECISION_TIMEOUT", raising=False)
    assert decision_client.decision_timeout_s() == pytest.approx(1.5)
    monkeypatch.setenv("KUREK_DECISION_TIMEOUT", "2.25")
    assert decision_client.decision_timeout_s() == pytest.approx(2.25)
    monkeypatch.setenv("KUREK_DECISION_TIMEOUT", "nope")
    assert decision_client.decision_timeout_s() == pytest.approx(1.5)


def _make_lite_engine(monkeypatch):
    """Build a KurekEngine without mic/STT/Whisper/Hyprland side effects."""
    import kurek_daemon as daemon

    monkeypatch.setattr(daemon, "_resolve_input_device", lambda: None)
    monkeypatch.setattr(
        daemon,
        "discover_actions",
        lambda *_a, **_k: SimpleNamespace(
            names=lambda: [],
            get_tool_declarations=lambda: [],
            run=lambda *_a, **_k: "ok",
        ),
    )
    monkeypatch.setattr(daemon, "get_deepgram_key", lambda: None)
    monkeypatch.setattr(daemon, "get_xai_key", lambda: None)
    monkeypatch.setattr(daemon, "WhisperSTT", lambda **_k: None)

    class _FakeTTS:
        def __init__(self):
            self.is_playing = False

        def speak(self, text: str):
            pass

        def speak_chunk(self, text: str, append: bool = False):
            pass

        def begin_stream(self):
            pass

        def enqueue(self, text: str, play_local: bool = True, on_audio=None):
            if play_local:
                self.speak_chunk(text)
            if on_audio is not None:
                on_audio(None)

        def wait_stream(self, timeout: float = 60.0) -> bool:
            return True

        def synthesize(self, text: str):
            return None

        def stop(self):
            self.is_playing = False

    monkeypatch.setattr(daemon, "create_tts_player", lambda *_a, **_k: _FakeTTS())
    monkeypatch.setattr(
        daemon, "get_mpv_sink", lambda: SimpleNamespace(ensure_running=lambda: None)
    )
    monkeypatch.setattr(daemon, "get_hyprland_watcher", lambda: None)
    monkeypatch.setattr(daemon.KurekEngine, "_report_startup_problems", lambda self, *_a: None)
    monkeypatch.setattr(
        daemon.KurekEngine, "_hourly_consolidation_loop", lambda self: None
    )
    monkeypatch.setattr(daemon.KurekEngine, "_notify_state", lambda self, _s: None)
    monkeypatch.setattr(daemon, "load_memory", lambda: {})
    monkeypatch.setattr(daemon, "format_memory_for_prompt", lambda *_a, **_k: "")
    monkeypatch.setattr(daemon, "resolve_hermes_memory_dirs", lambda: [])
    monkeypatch.setattr(daemon, "get_user_name", lambda: "Tester")

    engine = daemon.KurekEngine()
    engine.history = []
    engine.openai_tools = []
    engine.stt = None
    return engine


def _stub_screen_capture(monkeypatch, captured: list) -> None:
    import actions.screen_vision as sv

    def capture():
        captured.append("capture")
        return b"jpeg-bytes", None

    monkeypatch.setattr(sv, "capture_screen_jpeg", capture)
    monkeypatch.setattr(
        sv, "query_vision", lambda *_a, **_k: "compiler error on line 12"
    )
    monkeypatch.setattr(
        sv, "get_hyprland_context", lambda: {"title": "nvim", "class": "kitty"}
    )


def test_check_auto_screen_p09_triggers(monkeypatch, capsys) -> None:
    import kurek_daemon as daemon

    engine = _make_lite_engine(monkeypatch)
    monkeypatch.setattr(daemon, "auto_screen_enabled", lambda: True)
    monkeypatch.setattr(daemon, "get_openrouter_api_key", lambda: SECRET_KEY)
    monkeypatch.setattr(daemon, "get_typesafe_key", lambda: None)
    monkeypatch.setattr(daemon, "load_api_keys", lambda: {})
    monkeypatch.setattr(
        daemon,
        "query_openrouter_needs_screen",
        lambda *_a, **_k: 0.9,
    )
    captured: list = []
    _stub_screen_capture(monkeypatch, captured)

    result = engine._check_auto_screen_context("what error is on my screen?")
    assert result is not None
    assert "AUTOMATIC LIVE SCREEN CONTEXT" in result
    assert "compiler error" in result
    assert captured == ["capture"]
    out = capsys.readouterr().out
    assert "Microsoft-Decision-1" in out
    assert "P=0.90" in out
    assert SECRET_KEY not in out


def test_check_auto_screen_p02_does_not_trigger(monkeypatch) -> None:
    import kurek_daemon as daemon

    engine = _make_lite_engine(monkeypatch)
    monkeypatch.setattr(daemon, "auto_screen_enabled", lambda: True)
    monkeypatch.setattr(daemon, "get_openrouter_api_key", lambda: SECRET_KEY)
    monkeypatch.setattr(daemon, "get_typesafe_key", lambda: None)
    monkeypatch.setattr(daemon, "load_api_keys", lambda: {})
    monkeypatch.setattr(
        daemon,
        "query_openrouter_needs_screen",
        lambda *_a, **_k: 0.2,
    )
    captured: list = []
    _stub_screen_capture(monkeypatch, captured)

    assert engine._check_auto_screen_context("tell me a joke") is None
    assert captured == []


def test_malformed_openrouter_falls_back_to_typesafe(monkeypatch, capsys) -> None:
    import kurek_daemon as daemon

    engine = _make_lite_engine(monkeypatch)
    monkeypatch.setattr(daemon, "auto_screen_enabled", lambda: True)
    monkeypatch.setattr(daemon, "get_openrouter_api_key", lambda: SECRET_KEY)
    monkeypatch.setattr(daemon, "get_typesafe_key", lambda: "ts-test-key")
    monkeypatch.setattr(daemon, "load_api_keys", lambda: {})
    monkeypatch.setattr(
        daemon,
        "query_openrouter_needs_screen",
        lambda *_a, **_k: None,  # malformed / failed Decision-1
    )

    class _FakeNoul:
        def __init__(self, instructions: str):
            self.instructions = instructions

    class _FakeClient:
        def system_one(self, **_kwargs):
            return SimpleNamespace(
                answers={"needs_screen": SimpleNamespace(noul=0.88)}
            )

    monkeypatch.setitem(
        sys.modules,
        "typesafe_sdk",
        SimpleNamespace(TypeSafeClient=_FakeClient, Noul=_FakeNoul),
    )
    captured: list = []
    _stub_screen_capture(monkeypatch, captured)

    result = engine._check_auto_screen_context("fix this compiler error")
    assert result is not None
    assert captured == ["capture"]
    out = capsys.readouterr().out
    assert "TypeSafe Jev" in out
    assert SECRET_KEY not in out


def test_no_keys_skips_network(monkeypatch) -> None:
    import urllib.request

    import kurek_daemon as daemon

    engine = _make_lite_engine(monkeypatch)
    monkeypatch.setattr(daemon, "auto_screen_enabled", lambda: True)
    monkeypatch.setattr(daemon, "get_openrouter_api_key", lambda: None)
    monkeypatch.setattr(daemon, "get_typesafe_key", lambda: None)
    monkeypatch.setattr(daemon, "load_api_keys", lambda: {})

    def network_forbidden(*_a, **_k):
        raise AssertionError("urlopen must not be called when no keys are set")

    monkeypatch.setattr(urllib.request, "urlopen", network_forbidden)
    # Also ensure the helper itself never issues a request without a key.
    assert query_openrouter_needs_screen("hi", "", urlopen=network_forbidden) is None
    assert engine._check_auto_screen_context("what's on my screen?") is None


def test_opt_out_skips_decision_and_capture(monkeypatch) -> None:
    import kurek_daemon as daemon

    engine = _make_lite_engine(monkeypatch)
    monkeypatch.setenv("KUREK_AUTO_SCREEN", "0")
    # Ensure daemon uses real auto_screen_enabled (reads env)
    called = {"openrouter": False, "typesafe": False}

    def mark_or(*_a, **_k):
        called["openrouter"] = True
        return 0.99

    monkeypatch.setattr(daemon, "get_openrouter_api_key", lambda: SECRET_KEY)
    monkeypatch.setattr(daemon, "get_typesafe_key", lambda: "ts-key")
    monkeypatch.setattr(daemon, "query_openrouter_needs_screen", mark_or)
    monkeypatch.setattr(daemon, "load_api_keys", lambda: {})
    captured: list = []
    _stub_screen_capture(monkeypatch, captured)

    assert engine._check_auto_screen_context("what's on my screen?") is None
    assert called["openrouter"] is False
    assert captured == []


def test_get_openrouter_api_key_from_env(monkeypatch) -> None:
    from memory.config_manager import get_openrouter_api_key

    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET_KEY)
    monkeypatch.setattr(
        "memory.config_manager.load_api_keys",
        lambda: {"OPENROUTER_API_KEY": SECRET_KEY},
    )
    assert get_openrouter_api_key() == SECRET_KEY
    monkeypatch.setattr("memory.config_manager.load_api_keys", lambda: {})
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert get_openrouter_api_key() is None
