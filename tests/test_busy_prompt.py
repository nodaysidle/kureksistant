"""C11: a new prompt waits as long as a tool may run; when it still cannot run, Kurek says so and keeps the words."""
from __future__ import annotations

import inspect

import kurek_daemon as daemon
from core.action_loader import ActionRegistry
from tests.test_stream_errors import _make_lite_engine

BUSY = "Still finishing the previous request. Try again in a moment."


def test_query_wait_outlasts_a_tool_and_a_deepseek_read() -> None:
    assert inspect.signature(ActionRegistry.run).parameters["timeout"].default + 30 <= daemon.QUERY_WAIT_S


def _busy_engine(monkeypatch):
    engine = _make_lite_engine(monkeypatch)
    notes: list[str] = []
    monkeypatch.setattr(engine, "_notify_failure", notes.append)
    monkeypatch.setattr(daemon, "QUERY_WAIT_S", 0.1)
    return engine, notes


def test_a_dropped_local_prompt_is_said_aloud_and_its_words_kept(monkeypatch) -> None:
    engine, notes = _busy_engine(monkeypatch)
    engine._engine_lock.acquire()
    try:
        assert engine.handle_text_query("open the notes app") is None
    finally:
        engine._engine_lock.release()
    assert engine.tts.spoken[-1] == BUSY
    assert "open the notes app" in notes[0]
    assert engine.history == []


def test_a_dropped_phone_prompt_is_not_spoken_on_the_pc(monkeypatch) -> None:
    engine, notes = _busy_engine(monkeypatch)
    events: list[dict] = []
    engine._engine_lock.acquire()
    try:
        assert engine.handle_text_query("open the notes app", callback=events.append, mute_speaker=True) is None
    finally:
        engine._engine_lock.release()
    assert engine.tts.spoken == []
    assert [e["type"] for e in events] == ["error", "done"]
    assert "open the notes app" in notes[0]


def test_the_busy_line_is_not_spoken_while_the_owner_records(monkeypatch) -> None:
    engine, notes = _busy_engine(monkeypatch)
    engine.is_recording = True
    engine._engine_lock.acquire()
    try:
        assert engine.handle_text_query("open the notes app") is None
    finally:
        engine._engine_lock.release()
    assert engine.tts.spoken == []
    assert "open the notes app" in notes[0]
