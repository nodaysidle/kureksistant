"""Latency defaults: a shorter end-of-speech wait, no decision call before every
answer, reused HTTPS connections, and tools that don't wait for speech audio."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.llm_client as llm_client
import core.tts as tts_mod
import kurek_daemon as daemon
from core.decision_client import auto_screen_enabled
from tests.test_stream_errors import _FakeResponse, _make_lite_engine


@pytest.mark.parametrize("spoken,window", [(0, 3.0), (5, 4.5), (60, 5.0)])
def test_silence_window(spoken, window):
    assert daemon.effective_silence_window(100.0 + spoken, 100.0) == pytest.approx(window)


def test_auto_screen_is_opt_in(monkeypatch):
    monkeypatch.delenv("KUREK_AUTO_SCREEN", raising=False)
    assert not auto_screen_enabled()
    monkeypatch.setenv("KUREK_AUTO_SCREEN", "1")
    assert auto_screen_enabled()


def _no_bare_post(*_a, **_k):
    raise AssertionError("a fresh connection was opened instead of the shared session")


def test_deepseek_reuses_one_session(monkeypatch):
    monkeypatch.setattr("memory.config_manager.get_deepseek_key", lambda: "sk-test")
    monkeypatch.setattr(llm_client.requests, "post", _no_bare_post)
    calls = []

    def fake_post(*_a, **_k):
        calls.append(1)
        return _FakeResponse(lines=[
            b'data: {"choices":[{"delta":{"content":"Hi."},"finish_reason":"stop"}]}',
            b"data: [DONE]",
        ])

    monkeypatch.setattr(llm_client._HTTP, "post", fake_post)
    for _ in range(2):
        events = list(llm_client.stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
        assert events[-1]["type"] == "done"
    assert len(calls) == 2


def test_xai_tts_reuses_one_session(monkeypatch):
    monkeypatch.setattr(tts_mod.requests, "post", _no_bare_post)
    engine = tts_mod.XAITTSEngine(api_key="xai-test")
    sessions = []

    def fake_post(*_a, **_k):
        sessions.append(engine._http)
        return SimpleNamespace(status_code=200, content=b"ID3audio", text="")

    monkeypatch.setattr(engine._http, "post", fake_post)
    assert engine.synthesize("One.") == b"ID3audio"
    assert engine.synthesize("Two.") == b"ID3audio"
    assert len(sessions) == 2 and sessions[0] is sessions[1]


def test_tool_runs_before_waiting_for_speech(monkeypatch, tmp_path):
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"
    order: list[str] = []
    engine.tts.wait_stream = lambda timeout=60.0: (order.append("wait"), True)[1]
    engine._run_tool = lambda name, args, remote=False: (order.append("tool"), "ok")[1]
    calls = {"n": 0}

    def fake_stream(**_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {"type": "sentence", "sentence": "Let me check that."}
            yield {"type": "tool_calls", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "web_search", "arguments": "{}"}}]}
        else:
            yield {"type": "done", "full_content": "done"}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    engine.handle_text_query("q", callback=lambda e: None, stream_audio=True)
    assert order[:2] == ["tool", "wait"]
