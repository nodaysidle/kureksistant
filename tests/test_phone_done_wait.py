"""C7: with stream_audio, "done" goes to the phone only after every sentence's audio, unless a cancel
or TTS_STALL_S without progress ends the wait."""
from __future__ import annotations

import threading
import time

import kurek_daemon as daemon
from tests.test_stream_errors import _make_lite_engine


class _PhoneTTS:
    """enqueue keeps on_audio; each wait_stream() call delivers one pending sentence (or none)."""

    def __init__(self, deliver: bool = True, moving: bool = False) -> None:
        self.deliver, self.moving = deliver, moving
        self.pending: list = []
        self.delivered = 0
        self.calls = 0
        self.waiting = threading.Event()
        self.is_playing = False

    def begin_stream(self) -> None:
        pass

    def enqueue(self, text: str, play_local: bool = True, on_audio=None) -> None:
        if on_audio is not None:
            self.pending.append(on_audio)

    def wait_stream(self, timeout: float = 60.0) -> bool:
        self.calls += 1
        self.waiting.set()
        if not self.deliver:
            return False
        if self.pending:
            self.pending.pop(0)(b"x")
            self.delivered += 1
        return not self.pending

    def progress(self):
        return self.calls if self.moving else self.delivered

    def synthesize(self, text: str):
        return None

    def speak(self, text: str) -> None:
        pass

    def stop(self) -> None:
        pass


def _turn(monkeypatch, tmp_path, tts):
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "h.json"
    engine.tts = tts
    monkeypatch.setattr(daemon, "stream_deepseek_sentences", lambda **_k: iter([
        {"type": "sentence", "sentence": "One."},
        {"type": "sentence", "sentence": "Two."},
        {"type": "sentence", "sentence": "Three."},
        {"type": "done", "full_content": "One. Two. Three."},
    ]))
    events: list[dict] = []
    return engine, events, lambda: engine.handle_text_query(
        "count", callback=events.append, mute_speaker=True, stream_audio=True)


def test_done_waits_for_every_sentence(monkeypatch, tmp_path) -> None:
    _engine, events, run = _turn(monkeypatch, tmp_path, _PhoneTTS())
    run()
    assert [e["type"] for e in events] == ["sentence", "sentence", "sentence", "done"]


def test_a_stalled_voice_still_ends_with_done(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(daemon, "TTS_STALL_S", 0.2)
    _engine, events, run = _turn(monkeypatch, tmp_path, _PhoneTTS(deliver=False))
    started = time.monotonic()
    run()
    assert time.monotonic() - started < 1
    assert events[-1] == {"type": "done", "reply": "One. Two. Three."}


def test_a_cancel_ends_the_wait(monkeypatch, tmp_path) -> None:
    tts = _PhoneTTS(deliver=False, moving=True)
    engine, _events, run = _turn(monkeypatch, tmp_path, tts)

    def cancel() -> None:
        assert tts.waiting.wait(5)
        time.sleep(0.1)
        engine._cancel_requested = True

    timer = threading.Thread(target=cancel)
    timer.start()
    started = time.monotonic()
    try:
        run()
    finally:
        timer.join(5)
    assert time.monotonic() - started < 1
