"""C1: a reply that keeps playing is never cut by a timer; only a barge-in or TTS_STALL_S without any
progress stops it. Fakes only: no real mpv, no audio device."""
from __future__ import annotations

import socket
import threading
import time
from types import SimpleNamespace

import pytest

import core.mpv_sink
import core.tts as tts_mod
import kurek_daemon as daemon
from tests.test_daemon_pipeline import _SynthEngine, played  # noqa: F401  (fixture)
from tests.test_stream_errors import _make_lite_engine


class _DrainTTS:
    """Plays after the first enqueue: is_playing counts its polls; progress() is given."""

    def __init__(self, play_polls: int | None, progress) -> None:
        self.play_polls, self._progress = play_polls, progress
        self.started = threading.Event()
        self.polls = 0
        self.stops = 0
        self.spoken: list[str] = []

    @property
    def is_playing(self) -> bool:
        if not self.started.is_set():
            return False
        self.polls += 1
        return self.play_polls is None or self.polls <= self.play_polls

    def progress(self):
        return self._progress(self)

    def begin_stream(self) -> None:
        pass

    def enqueue(self, text: str, play_local: bool = True, on_audio=None) -> None:
        self.spoken.append(text)
        self.started.set()

    def wait_stream(self, timeout: float = 60.0) -> bool:
        return True

    def synthesize(self, text: str):
        return None

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def stop(self) -> None:
        self.stops += 1
        self.started.clear()


def _engine(monkeypatch, tmp_path, tts):
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "h.json"
    engine.tts = tts
    notes: list[str] = []
    monkeypatch.setattr(engine, "_notify_failure", notes.append)
    monkeypatch.setattr(daemon, "stream_deepseek_sentences", lambda **_k: iter([
        {"type": "sentence", "sentence": "A long answer.", "full_content": "A long answer."},
        {"type": "done", "full_content": "A long answer."},
    ]))
    return engine, notes


def _fake_clock(monkeypatch) -> None:
    """Every time.time()/monotonic() call in the daemon moves 10 s on; sleep returns at once."""
    t = [0.0]

    def clock() -> float:
        t[0] += 10.0
        return t[0]

    real = {n: getattr(time, n) for n in dir(time) if not n.startswith("_")}
    monkeypatch.setattr(daemon, "time", SimpleNamespace(**{**real, "time": clock, "monotonic": clock,
                                                            "sleep": lambda _s: None}))


def test_a_reply_that_keeps_playing_is_not_cut(monkeypatch, tmp_path) -> None:
    tts = _DrainTTS(play_polls=60, progress=lambda fake: fake.polls)
    engine, notes = _engine(monkeypatch, tmp_path, tts)
    _fake_clock(monkeypatch)
    engine.handle_text_query("tell me a long story")
    assert tts.polls > 60
    assert tts.stops == 0
    assert notes == []


def test_speech_that_makes_no_progress_is_stopped_and_reported(monkeypatch, tmp_path) -> None:
    tts = _DrainTTS(play_polls=None, progress=lambda _fake: 7)
    engine, notes = _engine(monkeypatch, tmp_path, tts)
    _fake_clock(monkeypatch)
    engine.handle_text_query("tell me a long story")
    assert tts.stops == 1
    assert notes == ["Speech got stuck, so I stopped it."]


def test_a_barge_in_ends_the_drain_at_once(monkeypatch, tmp_path) -> None:
    tts = _DrainTTS(play_polls=None, progress=lambda fake: fake.polls)
    engine, _notes = _engine(monkeypatch, tmp_path, tts)

    def barge_in() -> None:
        assert tts.started.wait(5)
        time.sleep(0.1)
        engine._cancel_requested = True

    timer = threading.Thread(target=barge_in)
    timer.start()
    started = time.monotonic()
    try:
        engine.handle_text_query("tell me a long story")
    finally:
        timer.join(5)
    assert time.monotonic() - started < 1
    assert tts.stops == 0


def _fake_mpv(path, data_json: str, connections: int) -> threading.Thread:
    """Answers each request the way mpv can: an event line first, then the reply."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(connections)
    server.settimeout(5)

    def run() -> None:
        try:
            for _ in range(connections):
                conn, _ = server.accept()
                with conn:
                    conn.settimeout(5)
                    buf = b""
                    while b"\n" not in buf:
                        chunk = conn.recv(1024)
                        if not chunk:
                            break
                        buf += chunk
                    conn.sendall(b'{"event":"playback-restart"}\n'
                                 + f'{{"data":{data_json},"request_id":1,"error":"success"}}\n'.encode())
        finally:
            server.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def test_mpv_replies_are_found_after_event_lines(tmp_path) -> None:
    sock = tmp_path / "mpv.sock"
    server = _fake_mpv(sock, "1.5", 1)
    try:
        assert core.mpv_sink.MpvPipeWireSink(sock_path=sock).position() == 1.5
    finally:
        server.join(5)
    sock.unlink()
    server = _fake_mpv(sock, "false", 1)
    try:
        assert core.mpv_sink.MpvPipeWireSink(sock_path=sock).is_playing() is True
    finally:
        server.join(5)
    sock.unlink()
    absent = core.mpv_sink.MpvPipeWireSink(sock_path=sock)
    assert absent.position() is None
    assert absent.is_playing() is False


class _Sink:
    def __init__(self) -> None:
        self.stops = 0

    def is_playing(self) -> bool:
        return False

    def position(self):
        return None

    def stop(self) -> None:
        self.stops += 1


@pytest.fixture
def sink(monkeypatch) -> _Sink:
    fake = _Sink()
    monkeypatch.setattr(core.mpv_sink, "get_mpv_sink", lambda: fake)
    monkeypatch.setattr(tts_mod.sd, "stop", lambda: None)
    return fake


def test_player_progress_counts_synthesis_and_finished_sentences(sink, played) -> None:  # noqa: F811
    player = tts_mod.TTSPlayer(_SynthEngine())
    assert player.progress() == (0, None)
    player.enqueue("One.")
    player.enqueue("Two.")
    assert player.wait_stream(5)
    assert player.progress() == (4, None)


class _GateSynth(_SynthEngine):
    def __init__(self, gate: threading.Event, blocked_prefix: str) -> None:
        super().__init__()
        self.gate, self.blocked_prefix = gate, blocked_prefix

    def synthesize(self, text: str) -> bytes:
        if text.startswith(self.blocked_prefix):
            assert self.gate.wait(5)
        return super().synthesize(text)


def test_a_new_prompt_still_stops_a_draining_reply_at_once(monkeypatch, tmp_path, sink, played) -> None:  # noqa: F811
    gate = threading.Event()
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "h.json"
    engine.tts = tts_mod.TTSPlayer(_GateSynth(gate, "Old"))
    scripts = [
        [{"type": "sentence", "sentence": "Old one."}, {"type": "sentence", "sentence": "Old two."},
         {"type": "done", "full_content": "Old one. Old two."}],
        [{"type": "sentence", "sentence": "New."}, {"type": "done", "full_content": "New."}],
    ]
    monkeypatch.setattr(daemon, "stream_deepseek_sentences", lambda **_k: iter(scripts.pop(0)))
    first = threading.Thread(target=engine.handle_text_query, args=("first",))
    second = threading.Thread(target=engine.handle_text_query, args=("second",))
    first.start()
    try:
        deadline = time.monotonic() + 2
        while not (engine._speaker_lock.locked() and not engine._engine_lock.locked() and not scripts[1:]):
            assert time.monotonic() < deadline, "the first turn never reached its drain"
            time.sleep(0.01)
        second.start()
        deadline = time.monotonic() + 0.5
        while sink.stops == 0:
            assert time.monotonic() < deadline, "the new prompt did not stop the old reply"
            time.sleep(0.01)
    finally:
        gate.set()
        first.join(5)
        if second.ident is not None:  # started
            second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert [audio for audio, _append in played] == [b"New."]


def test_stop_still_clears_the_queue(sink, played) -> None:  # noqa: F811
    gate = threading.Event()
    player = tts_mod.TTSPlayer(_GateSynth(gate, ""))
    for text in ("A.", "B.", "C."):
        player.enqueue(text)
    time.sleep(0.05)
    player.stop()
    assert player._queue.empty()
    assert sink.stops == 1
    gate.set()
    assert player.wait_stream(5)
    assert played == []


def _polls(answers: list[bool]):
    """busy() that gives these answers in turn, then False; and how many times it was asked."""
    asked: list[bool] = []

    def busy() -> bool:
        answer = answers[len(asked)] if len(asked) < len(answers) else False
        asked.append(answer)
        return answer

    return busy, asked


def _bare_engine():
    engine = object.__new__(daemon.KurekEngine)
    engine.tts = SimpleNamespace()  # no progress(): busy() and cancelled() decide
    return engine


def test_one_missed_playing_answer_does_not_end_the_wait() -> None:
    busy, asked = _polls([True, False, True, True, False, False])  # one late mpv reply read as "not playing"
    assert _bare_engine()._wait_speech(busy, lambda: False) is True
    assert asked == [True, False, True, True, False, False]


def test_finished_speech_ends_the_wait_after_two_polls() -> None:
    busy, asked = _polls([])
    started = time.monotonic()
    assert _bare_engine()._wait_speech(busy, lambda: False) is True
    assert asked == [False, False]
    assert time.monotonic() - started < 0.5
