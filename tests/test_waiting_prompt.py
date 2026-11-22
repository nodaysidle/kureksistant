"""A1: a prompt that waited for the engine is never answered into a new recording or after a newer
prompt or click; its words stay in a notification. A click that stops a recording silences Kurek first.
Fakes only: no microphone, mpv or network."""
from __future__ import annotations

import threading
import time

import numpy as np

import kurek_daemon as daemon
from tests.test_stream_errors import _make_lite_engine


class _Stream:
    def __init__(self, **_kw) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


class _TTS:
    """Logs every call with whether the owner was recording at that moment."""

    def __init__(self, engine, log: list) -> None:
        self.engine, self.log, self.is_playing = engine, log, False

    def begin_stream(self) -> None:
        pass

    def enqueue(self, text: str, play_local: bool = True, on_audio=None) -> None:
        self.log.append(("enqueue", text, self.engine.is_recording))

    def speak(self, text: str) -> None:
        self.log.append(("speak", text, self.engine.is_recording))

    def wait_stream(self, timeout: float = 60.0) -> bool:
        return True

    def synthesize(self, text: str):
        return None

    def stop(self) -> None:
        self.log.append(("stop",))
        self.is_playing = False


def _engine(monkeypatch):
    engine = _make_lite_engine(monkeypatch)
    monkeypatch.setattr(daemon.sd, "InputStream", _Stream)
    log: list = []
    engine.tts = _TTS(engine, log)
    notes: list[str] = []
    monkeypatch.setattr(engine, "_notify_failure", notes.append)

    def answer(**kwargs):
        asked = [m for m in kwargs["messages"] if m.get("role") == "user"][-1]["content"].split("\n")[0]
        yield {"type": "sentence", "sentence": f"Answer to {asked}.", "full_content": f"Answer to {asked}."}
        yield {"type": "done", "full_content": f"Answer to {asked}."}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", answer)
    return engine, log, notes


def _wait_for(cond, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def _waiting(engine, prompt: str, **kwargs) -> threading.Thread:
    t = threading.Thread(target=engine.handle_text_query, args=(prompt,), kwargs=kwargs, daemon=True)
    t.start()
    time.sleep(0.15)  # it is now waiting for the engine
    return t


def test_a_waiting_prompt_is_not_answered_into_a_new_recording(monkeypatch) -> None:
    engine, log, notes = _engine(monkeypatch)
    engine._engine_lock.acquire()  # a long tool turn holds the engine
    engine.set_state(daemon.KurekState.THINKING)
    waiting = _waiting(engine, "what is the weather")
    engine.toggle()  # a click: barge-in
    engine.toggle()  # a click: he starts a new recording
    assert engine.is_recording
    engine._engine_lock.release()  # the tool turn ends
    waiting.join(5)
    assert not waiting.is_alive()
    assert [e for e in log if e[0] in ("enqueue", "speak")] == []
    assert notes == ["Not answered: what is the weather"]
    assert engine.is_recording and engine.state == daemon.KurekState.LISTENING


def test_a_recording_alone_also_stops_the_waiting_prompt(monkeypatch) -> None:
    engine, log, notes = _engine(monkeypatch)
    engine._engine_lock.acquire()
    waiting = _waiting(engine, "a long typed prompt " + "word " * 200)
    engine.toggle()  # IDLE: the click starts a recording, no barge-in
    assert engine.is_recording
    engine._engine_lock.release()
    waiting.join(5)
    assert [e for e in log if e[0] in ("enqueue", "speak")] == []
    assert notes == ["Not answered: a long typed prompt " + "word " * 200]  # his full words


def test_a_superseded_waiting_prompt_gives_way_to_the_newer_one(monkeypatch) -> None:
    engine, log, notes = _engine(monkeypatch)
    engine._engine_lock.acquire()
    first = _waiting(engine, "delete the draft")
    second = _waiting(engine, "no wait, keep the draft")
    engine._engine_lock.release()
    first.join(5)
    second.join(5)
    assert [e[1] for e in log if e[0] == "enqueue"] == ["Answer to no wait, keep the draft."]
    assert notes == ["Not answered: delete the draft"]


def test_a_dropped_phone_prompt_that_speaks_on_the_pc_ends_its_stream(monkeypatch) -> None:
    engine, log, notes = _engine(monkeypatch)
    events: list[dict] = []
    engine._engine_lock.acquire()
    waiting = _waiting(engine, "read my mail", callback=events.append, remote=True)
    engine.toggle()  # he starts recording on the PC
    engine._engine_lock.release()
    waiting.join(5)
    assert [e["type"] for e in events] == ["error", "done"]
    assert notes == ["Not answered: read my mail"]
    assert [e for e in log if e[0] in ("enqueue", "speak")] == []


def test_a_waiting_prompt_that_nothing_superseded_still_runs(monkeypatch) -> None:
    engine, log, notes = _engine(monkeypatch)
    engine._engine_lock.acquire()
    waiting = _waiting(engine, "what is the weather")
    engine._engine_lock.release()
    waiting.join(5)
    assert ("enqueue", "Answer to what is the weather.", False) in log
    assert notes == []


def test_a_click_that_stops_a_recording_silences_kurek_first(monkeypatch) -> None:
    engine, log, _notes = _engine(monkeypatch)
    engine.toggle()  # start recording
    assert engine.state == daemon.KurekState.LISTENING
    engine.audio_buffer = [np.zeros(1600, dtype=np.float32)]
    engine.tts.is_playing = True  # speech is audible while he records
    log.clear()
    engine._timed_process_recorded_audio = lambda t0: log.append(("stt",))
    engine.toggle()  # his click to stop
    assert _wait_for(lambda: ("stt",) in log)
    assert log.index(("stop",)) < log.index(("stt",))
    assert not engine.tts.is_playing
