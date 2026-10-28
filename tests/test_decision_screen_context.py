"""Offline tests for auto screen-context gating (Jev → Decision-1 on OpenRouter)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import core.decision_client as decision_client

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
    monkeypatch.setattr(daemon, "load_api_keys", lambda: {})
    monkeypatch.setattr(
        daemon,
        "query_needs_screen",
        lambda *_a, **_k: (0.9, "Microsoft-Decision-1"),
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
    monkeypatch.setattr(daemon, "load_api_keys", lambda: {})
    monkeypatch.setattr(
        daemon,
        "query_needs_screen",
        lambda *_a, **_k: (0.2, "Microsoft-Decision-1"),
    )
    captured: list = []
    _stub_screen_capture(monkeypatch, captured)

    assert engine._check_auto_screen_context("tell me a joke") is None
    assert captured == []


@pytest.mark.parametrize(
    ("noul", "expect_capture"),
    [(decision_client.SCREEN_CONTEXT_THRESHOLD + 0.1, True),
     (decision_client.SCREEN_CONTEXT_THRESHOLD - 0.1, False)],
)
def test_jev_noul_threshold_gates_capture(monkeypatch, capsys, noul, expect_capture) -> None:
    """Real decision_client over mocked HTTP: Python applies the threshold."""
    import urllib.request

    import kurek_daemon as daemon
    from memory import config_manager

    engine = _make_lite_engine(monkeypatch)
    monkeypatch.setattr(daemon, "auto_screen_enabled", lambda: True)
    monkeypatch.setattr(daemon, "load_api_keys", lambda: {})
    monkeypatch.setattr(config_manager, "get_openrouter_api_key", lambda: SECRET_KEY)
    monkeypatch.delenv("KUREK_DECISION_BUDGET", raising=False)
    models = []

    def fake_urlopen(req, timeout=None):
        models.append(json.loads(req.data.decode())["model"])
        return _FakeHTTPResponse(_noul_payload(noul))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    captured: list = []
    _stub_screen_capture(monkeypatch, captured)

    result = engine._check_auto_screen_context("fix this compiler error")
    assert models == [decision_client.JEV_MODEL]
    assert (result is not None) is expect_capture
    assert captured == (["capture"] if expect_capture else [])
    out = capsys.readouterr().out
    if expect_capture:
        assert "TypeSafe Jev" in out
    assert SECRET_KEY not in out


def test_no_keys_skips_network(monkeypatch) -> None:
    import urllib.request

    import kurek_daemon as daemon

    engine = _make_lite_engine(monkeypatch)
    monkeypatch.setattr(daemon, "auto_screen_enabled", lambda: True)
    monkeypatch.setattr(daemon, "get_openrouter_api_key", lambda: None)
    monkeypatch.setattr(daemon, "load_api_keys", lambda: {})
    # decision_client reads provider keys from config_manager, not the daemon
    from memory import config_manager

    monkeypatch.setattr(config_manager, "get_openrouter_api_key", lambda: None)

    def network_forbidden(*_a, **_k):
        raise AssertionError("urlopen must not be called when no keys are set")

    monkeypatch.setattr(urllib.request, "urlopen", network_forbidden)
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
    monkeypatch.setattr(daemon, "query_needs_screen", mark_or)
    monkeypatch.setattr(daemon, "load_api_keys", lambda: {})
    import urllib.request

    def network_forbidden(*_a, **_k):  # pragma: no cover - must not run
        raise AssertionError("KUREK_AUTO_SCREEN=0 must not touch the network")

    monkeypatch.setattr(urllib.request, "urlopen", network_forbidden)
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
