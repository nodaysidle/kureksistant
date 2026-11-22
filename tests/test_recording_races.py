"""C10: one microphone transition at a time, no other turn takes LISTENING away, and a late transcript
is shown instead of answered into a new recording. Fake input streams only: no audio device."""
from __future__ import annotations

import threading
import time

import numpy as np

import kurek_daemon as daemon
from tests.test_stream_errors import _make_lite_engine


class _Stream:
    def __init__(self, mic: "_Mic", callback) -> None:
        self.mic, self.callback = mic, callback

    def start(self) -> None:
        self.mic.log.append("start")

    def stop(self) -> None:
        self.mic.log.append("stop")
        if self.mic.on_stop is not None:
            self.mic.on_stop(self)
        if self.mic.stop_gate is not None:
            assert self.mic.stop_gate.wait(5)

    def close(self) -> None:
        self.mic.log.append("close")


class _Mic:
    """sd.InputStream stand-in: every stream's calls land in one log; optional gates and failure."""

    def __init__(self, init_gate=None, stop_gate=None, fail=None, on_stop=None, log=None) -> None:
        self.init_gate, self.stop_gate, self.fail, self.on_stop = init_gate, stop_gate, fail, on_stop
        self.log = [] if log is None else log
        self.created: list[_Stream] = []

    def __call__(self, **kw) -> _Stream:
        if self.init_gate is not None:
            assert self.init_gate.wait(5)
        if self.fail is not None:
            raise self.fail
        stream = _Stream(self, kw["callback"])
        self.created.append(stream)
        self.log.append("create")
        return stream


def _engine(monkeypatch, mic: _Mic):
    engine = _make_lite_engine(monkeypatch)
    monkeypatch.setattr(daemon.sd, "InputStream", mic)
    notes: list[str] = []
    monkeypatch.setattr(engine, "_notify_failure", notes.append)
    processed: list[list] = []
    engine._timed_process_recorded_audio = lambda t0: processed.append(list(engine.audio_buffer))
    return engine, notes, processed


def _wait_for(cond, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def test_a_double_click_while_the_mic_opens_starts_one_stream(monkeypatch) -> None:
    gate = threading.Event()
    mic = _Mic(init_gate=gate)
    engine, _notes, _processed = _engine(monkeypatch, mic)
    first = threading.Thread(target=engine.toggle)
    first.start()
    try:
        time.sleep(0.1)  # the first click is inside InputStream(...)
        started = time.monotonic()
        engine.toggle()
        assert time.monotonic() - started < 0.5
    finally:
        gate.set()
        first.join(5)
    assert not first.is_alive()
    assert len(mic.created) == 1
    assert mic.log == ["create", "start"]
    assert engine.is_recording
    assert engine.state == daemon.KurekState.LISTENING


def test_two_stops_at_once_process_the_recording_once(monkeypatch) -> None:
    gate = threading.Event()
    mic = _Mic(stop_gate=gate)
    engine, _notes, processed = _engine(monkeypatch, mic)
    engine.toggle()
    engine.audio_buffer.append(np.zeros(160, dtype=np.float32))
    first = threading.Thread(target=engine.stop_listening_and_process)
    first.start()
    try:
        assert _wait_for(lambda: "stop" in mic.log)
        started = time.monotonic()
        engine.stop_listening_and_process()
        assert time.monotonic() - started < 0.5
    finally:
        gate.set()
        first.join(5)
    assert not first.is_alive()
    assert _wait_for(lambda: len(processed) == 1)
    time.sleep(0.1)
    assert len(processed) == 1


def test_a_phone_turn_does_not_take_listening_away(monkeypatch) -> None:
    engine, _notes, _processed = _engine(monkeypatch, _Mic())
    engine.is_recording = True
    engine.state = daemon.KurekState.LISTENING
    monkeypatch.setattr(daemon, "stream_deepseek_sentences", lambda **_k: iter([
        {"type": "sentence", "sentence": "Done.", "full_content": "Done."},
        {"type": "done", "full_content": "Done."},
    ]))
    events: list[dict] = []
    engine.handle_text_query("from the phone", callback=events.append, mute_speaker=True)
    assert events[-1]["type"] == "done"
    assert engine.state == daemon.KurekState.LISTENING
    stops: list[int] = []
    monkeypatch.setattr(engine, "stop_listening_and_process", lambda: stops.append(1))
    engine.toggle()
    assert stops == [1]
    assert engine._cancel_requested is False


def test_a_late_transcript_is_shown_not_answered(monkeypatch) -> None:
    engine, notes, _processed = _engine(monkeypatch, _Mic())
    engine.audio_buffer = [np.zeros(160, dtype=np.float32)]

    class _LateSTT:
        def transcribe(self, _audio, sample_rate):
            engine.is_recording = True  # the owner clicked and is speaking again
            return "late words about the phone app and the long list of things it should do next"

    engine.stt = _LateSTT()
    answered: list[str] = []
    monkeypatch.setattr(engine, "handle_text_query", lambda text, **_k: answered.append(text))
    engine._process_recorded_audio()
    assert answered == []
    assert "late words about the phone app and the long list of things it should do next" in notes[0]
    assert engine.tts.spoken == []


def test_a_leftover_stream_is_closed_before_a_new_one_opens(monkeypatch) -> None:
    mic = _Mic()
    engine, _notes, _processed = _engine(monkeypatch, mic)
    engine.record_stream = _Stream(mic, callback=None)
    engine.toggle()
    assert mic.log[:3] == ["stop", "close", "create"]
    assert len(mic.created) == 1
    assert engine.record_stream is mic.created[0]


def test_a_mic_that_fails_to_open_is_not_left_recording(monkeypatch) -> None:
    engine, _notes, _processed = _engine(monkeypatch, _Mic(fail=RuntimeError("host API not found")))
    engine.toggle()
    assert engine.is_recording is False
