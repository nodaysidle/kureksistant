"""One turn at a time: a new prompt cancels the running one, and a cancelled
turn leaves no history and no stale speech behind."""
from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

import core.tts as tts_mod
import kurek_daemon as daemon
from tests.test_daemon_pipeline import _SynthEngine
from tests.test_stream_errors import _make_lite_engine


@pytest.fixture
def engine(monkeypatch, tmp_path):
    eng = _make_lite_engine(monkeypatch)
    eng.history_file = tmp_path / "hist.json"
    eng.history = []
    return eng


def test_new_prompt_cancels_running_turn(engine, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = {"n": 0}

    def fake_stream(**_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {"type": "sentence", "sentence": "First answer starts."}
            entered.set()
            release.wait(5)
            yield {"type": "sentence", "sentence": "First answer continues."}
            yield {"type": "done", "full_content": "First answer starts. First answer continues."}
        else:
            yield {"type": "sentence", "sentence": "Second answer."}
            yield {"type": "done", "full_content": "Second answer."}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)

    t1 = threading.Thread(target=engine.handle_text_query, args=("first",))
    t1.start()
    assert entered.wait(5)
    t2 = threading.Thread(target=engine.handle_text_query, args=("second",))
    t2.start()
    deadline = time.monotonic() + 5
    while not engine._cancel_requested and time.monotonic() < deadline:
        time.sleep(0.01)
    assert engine._cancel_requested, "the second prompt must cancel the first"
    release.set()
    t1.join(5)
    t2.join(5)
    assert not t1.is_alive() and not t2.is_alive()

    assert [(h["role"], h["content"]) for h in engine.history] == [
        ("user", "second"), ("assistant", "Second answer."),
    ]
    assert "First answer continues." not in engine.tts.spoken


def test_busy_lock_times_out_without_touching_history(engine, monkeypatch):
    monkeypatch.setattr(daemon, "QUERY_WAIT_S", 0.1)
    events: list[dict] = []
    engine._query_lock.acquire()
    try:
        assert engine.handle_text_query("hello", callback=events.append) is None
    finally:
        engine._query_lock.release()
    assert engine.history == []
    assert [e["type"] for e in events] == ["error", "done"]


def test_toggle_to_listen_keeps_a_running_turn_cancelled(engine, monkeypatch):
    monkeypatch.setattr(engine, "start_listening", lambda: None)
    engine.state = daemon.KurekState.IDLE
    engine._cancel_requested = True
    engine.toggle()
    assert engine._cancel_requested


def test_cancelled_turn_does_not_reset_listening_state(engine, monkeypatch):
    def fake_stream(**_kw):
        # The user clicked to listen again while this turn was still unwinding.
        engine._cancel_requested = True
        engine.state = daemon.KurekState.LISTENING
        yield {"type": "done", "full_content": "late"}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    engine.handle_text_query("hi")
    assert engine.state == daemon.KurekState.LISTENING
    assert engine.history == []


# -- TTS: a new turn or a failure message drops the old queue ---------------

@pytest.fixture
def played(monkeypatch):
    calls: list[tuple[bytes, bool]] = []
    monkeypatch.setattr(tts_mod, "_play_audio_bytes", lambda b, append=False: calls.append((b, append)))
    monkeypatch.setattr(tts_mod.sd, "stop", lambda: None)
    return calls


def _blocking_engine(gate: threading.Event):
    class _Blocking(_SynthEngine):
        def synthesize(self, text):
            gate.wait(5)
            return super().synthesize(text)

    return _Blocking()


def test_begin_stream_drops_sentences_from_the_previous_turn(played):
    gate = threading.Event()
    player = tts_mod.TTSPlayer(_blocking_engine(gate))
    for s in ("Old one.", "Old two.", "Old three."):
        player.enqueue(s)
    time.sleep(0.05)
    player.begin_stream()
    player.enqueue("New.")
    gate.set()
    assert player.wait_stream(timeout=5)
    assert [b for b, _ in played] == [b"New."]


def test_speak_drops_queued_sentences(played):
    gate = threading.Event()
    engine = _blocking_engine(gate)
    player = tts_mod.TTSPlayer(engine)
    for s in ("Queued one.", "Queued two."):
        player.enqueue(s)
    time.sleep(0.05)
    player.speak("Something went wrong.")
    gate.set()
    assert player.wait_stream(timeout=5)
    assert played == []
    assert engine.spoken == ["Something went wrong."]


def test_history_timestamps_still_written(engine, monkeypatch):
    def fake_stream(**_kw):
        yield {"type": "done", "full_content": "ok"}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    engine.handle_text_query("hi")
    assert engine.history[-1]["ts"][:4] == str(datetime.now().year)
