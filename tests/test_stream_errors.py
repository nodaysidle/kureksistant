"""Offline tests for stream_deepseek error events and daemon IDLE recovery."""
from __future__ import annotations

from types import SimpleNamespace

import core.llm_client as llm_client
from core.llm_client import stream_deepseek, stream_deepseek_sentences


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "", lines: list[bytes] | None = None):
        self.status_code = status_code
        self.text = text
        self._lines = lines or []

    def iter_lines(self):
        yield from self._lines


def test_stream_deepseek_yields_error_on_connection_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.config_manager.get_deepseek_key",
        lambda: "sk-test-not-real",
    )

    def boom(*_a, **_k):
        raise ConnectionError("network down")

    monkeypatch.setattr(llm_client.requests, "post", boom)

    events = list(stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "network down" in events[0]["error"]


def test_stream_deepseek_yields_error_on_http_401(monkeypatch) -> None:
    monkeypatch.setattr(
        "memory.config_manager.get_deepseek_key",
        lambda: "sk-test-not-real",
    )
    monkeypatch.setattr(
        llm_client.requests,
        "post",
        lambda *_a, **_k: _FakeResponse(status_code=401, text="Unauthorized"),
    )

    events = list(stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    assert events[0]["type"] == "error"
    assert "401" in events[0]["error"]


def test_stream_deepseek_yields_error_when_no_key(monkeypatch) -> None:
    monkeypatch.setattr("memory.config_manager.get_deepseek_key", lambda: None)
    events = list(stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    assert events[0]["type"] == "error"
    assert "DEEPSEEK_API_KEY" in events[0]["error"]


def test_stream_deepseek_sentences_forwards_error(monkeypatch) -> None:
    def fake_stream(**_kwargs):
        yield {"type": "error", "error": "HTTP 401: Unauthorized"}

    monkeypatch.setattr(llm_client, "stream_deepseek", fake_stream)
    events = list(stream_deepseek_sentences(messages=[{"role": "user", "content": "hi"}]))
    assert events == [{"type": "error", "error": "HTTP 401: Unauthorized"}]


def _make_lite_engine(monkeypatch):
    """Build a KurekEngine without mic/STT/Whisper/Hyprland side effects."""
    import kurek_daemon as daemon

    monkeypatch.setattr(daemon, "_resolve_input_device", lambda: None)
    monkeypatch.setattr(daemon, "discover_actions", lambda *_a, **_k: SimpleNamespace(
        names=lambda: [],
        get_tool_declarations=lambda: [],
        run=lambda *_a, **_k: "ok",
    ))
    monkeypatch.setattr(daemon, "get_deepgram_key", lambda: None)
    monkeypatch.setattr(daemon, "get_xai_key", lambda: None)
    monkeypatch.setattr(daemon, "WhisperSTT", lambda **_k: None)

    class _FakeTTS:
        def __init__(self):
            self.is_playing = False
            self.spoken: list[str] = []

        def speak(self, text: str):
            self.spoken.append(text)

        def speak_chunk(self, text: str, append: bool = False):
            self.spoken.append(text)

        def stop(self):
            self.is_playing = False

    monkeypatch.setattr(daemon, "create_tts_player", lambda *_a, **_k: _FakeTTS())
    monkeypatch.setattr(daemon, "get_mpv_sink", lambda: SimpleNamespace(ensure_running=lambda: None))
    monkeypatch.setattr(daemon, "get_hyprland_watcher", lambda: None)
    monkeypatch.setattr(daemon.KurekEngine, "_hourly_consolidation_loop", lambda self: None)
    monkeypatch.setattr(daemon.KurekEngine, "_check_auto_screen_context", lambda self, _p: None)
    monkeypatch.setattr(daemon.KurekEngine, "_notify_state", lambda self, _s: None)
    monkeypatch.setattr(daemon.KurekEngine, "_append_daily_memory", lambda self, *_a, **_k: None)
    monkeypatch.setattr(daemon.KurekEngine, "_consolidate_with_jev", lambda self, *_a, **_k: None)
    monkeypatch.setattr(daemon, "load_memory", lambda: {})
    monkeypatch.setattr(daemon, "format_memory_for_prompt", lambda *_a, **_k: "")
    monkeypatch.setattr(daemon, "resolve_hermes_memory_dirs", lambda: [])
    monkeypatch.setattr(daemon, "get_user_name", lambda: "Tester")

    engine = daemon.KurekEngine()
    engine.history = []
    engine.openai_tools = []
    engine.stt = None
    return engine


def test_handle_text_query_connection_error_resets_idle(monkeypatch) -> None:
    import kurek_daemon as daemon

    engine = _make_lite_engine(monkeypatch)
    history_before = list(engine.history)

    def boom_stream(**_kwargs):
        yield {"type": "error", "error": "ConnectionError: network down"}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", boom_stream)

    # Must not raise — state must return to IDLE.
    engine.handle_text_query("hello")
    assert engine.state == daemon.KurekState.IDLE
    assert engine.history == history_before
    assert engine.tts.spoken
    assert "All set." not in engine.tts.spoken
    assert any("DeepSeek" in s for s in engine.tts.spoken)


def test_handle_text_query_http_401_no_fake_success(monkeypatch) -> None:
    import kurek_daemon as daemon

    engine = _make_lite_engine(monkeypatch)

    def err_stream(**_kwargs):
        yield {"type": "error", "error": "HTTP 401: Unauthorized"}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", err_stream)
    engine.handle_text_query("status check")
    assert engine.state == daemon.KurekState.IDLE
    assert engine.history == []
    assert "All set." not in engine.tts.spoken
    assert any("DeepSeek" in s for s in engine.tts.spoken)


def test_handle_text_query_empty_stream_no_all_set(monkeypatch) -> None:
    import kurek_daemon as daemon

    engine = _make_lite_engine(monkeypatch)

    def empty_stream(**_kwargs):
        yield {"type": "done", "full_content": ""}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", empty_stream)
    engine.handle_text_query("say something")
    assert engine.state == daemon.KurekState.IDLE
    assert engine.history == []
    assert "All set." not in engine.tts.spoken
    assert any("response" in s.lower() or "DeepSeek" in s for s in engine.tts.spoken)


def test_handle_text_query_uncaught_exception_resets_idle(monkeypatch) -> None:
    import kurek_daemon as daemon

    engine = _make_lite_engine(monkeypatch)

    def explode(**_kwargs):
        raise ConnectionError("socket closed")
        yield  # pragma: no cover — make this a generator

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", explode)
    engine.handle_text_query("ping")
    assert engine.state == daemon.KurekState.IDLE
