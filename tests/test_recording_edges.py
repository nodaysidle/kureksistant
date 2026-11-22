"""C9: "Speak now" only once the stream runs (first syllable), the buffers PortAudio delivers during
stop() are kept (last syllable), overflows are logged once and a mic that fails is shown."""
from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np

import kurek_daemon as daemon
from tests.test_recording_races import _engine, _Mic


def _recorder(monkeypatch, mic: _Mic):
    engine, notes, processed = _engine(monkeypatch, mic)
    monkeypatch.setattr(engine, "_notify_state", mic.log.append)
    return engine, notes, processed


def test_listening_is_shown_only_after_the_stream_started(monkeypatch) -> None:
    mic = _Mic()
    engine, _notes, _processed = _recorder(monkeypatch, mic)
    engine.toggle()
    assert mic.log == ["create", "start", "listening"]


def test_buffers_delivered_during_stop_are_kept(monkeypatch) -> None:
    def last_buffer(stream):
        stream.callback(np.full((160, 1), 0.5, dtype=np.float32), 160, None, None)

    engine, _notes, processed = _recorder(monkeypatch, _Mic(on_stop=last_buffer))
    engine.toggle()
    engine.toggle()
    deadline = time.monotonic() + 2
    while not processed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert processed, "the recording was not processed"
    assert np.all(processed[0][-1] == 0.5)


def test_input_overflows_are_logged_once_at_stop(monkeypatch, capsys) -> None:
    mic = _Mic()
    engine, _notes, _processed = _recorder(monkeypatch, mic)
    engine.toggle()
    for _ in range(3):
        mic.created[0].callback(np.zeros((160, 1), dtype=np.float32), 160, None,
                                SimpleNamespace(input_overflow=True))
    capsys.readouterr()
    engine.toggle()
    assert capsys.readouterr().out.count("[Kurek Mic] 3 input overflow(s)") == 1


def test_a_mic_error_is_shown_and_listening_never_announced(monkeypatch) -> None:
    mic = _Mic(fail=RuntimeError("Error opening InputStream"))
    engine, notes, _processed = _recorder(monkeypatch, mic)
    engine.toggle()
    assert notes == ["The microphone did not start: Error opening InputStream"]
    assert "listening" not in mic.log
    assert engine.state == daemon.KurekState.IDLE
    assert engine.is_recording is False
