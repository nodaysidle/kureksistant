"""C2: background messages (resource alert, finished job, screen watch) wait for a quiet moment and
never interrupt a reply or a recording; the owner's click or new prompt stops them like any reply."""
from __future__ import annotations

import threading
import time

import pytest

import core.mpv_sink
import core.tts as tts_mod
import kurek_daemon as daemon
from actions import process_sentinel
from actions import screen_vision as sv
from actions.system_monitor import _GLOBAL_WATCHER
from tests.test_daemon_pipeline import _SynthEngine, played  # noqa: F401  (fixture)
from tests.test_speech_drain import _Sink
from tests.test_stream_errors import _make_lite_engine


@pytest.fixture(autouse=True)
def _no_stale_hooks(monkeypatch):
    """Engine construction sets both module-level hooks; restore them so no later test reaches this engine."""
    monkeypatch.setattr(tts_mod, "_announcer", None, raising=False)
    monkeypatch.setattr(_GLOBAL_WATCHER, "alert_callback", None)
    yield
    for t in threading.enumerate():
        if t.name == "kurek-announce":
            t.join(2)
    assert [t for t in threading.enumerate() if t.name == "kurek-announce"] == []


class _AnnounceTTS:
    def __init__(self) -> None:
        self.calls: list = []
        self.is_playing = False

    def enqueue(self, text: str, play_local: bool = True, on_audio=None) -> None:
        self.calls.append(("enqueue", text))
        self.is_playing = True

    def begin_stream(self) -> None:
        self.calls.append("begin_stream")

    def speak(self, text: str) -> None:
        self.calls.append("speak")

    def stop(self) -> None:
        self.calls.append("stop")
        self.is_playing = False

    def wait_stream(self, timeout: float = 60.0) -> bool:
        return True

    def synthesize(self, text: str):
        return None


def _engine(monkeypatch, tmp_path):
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "h.json"
    engine.tts = _AnnounceTTS()
    notes: list[str] = []
    monkeypatch.setattr(engine, "_notify_failure", notes.append)
    return engine, notes


def _announce(engine, text: str):
    result: dict = {}
    thread = threading.Thread(target=lambda: result.setdefault("ok", engine.announce(text)),
                              name="kurek-announce", daemon=True)
    thread.start()
    return thread, result


def _wait_for(cond, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def _playing(engine, text: str):
    """An announcement that is playing: state speaking, speaker lock held by announce()."""
    thread, result = _announce(engine, text)
    assert _wait_for(lambda: ("enqueue", text) in engine.tts.calls)
    assert engine.state == daemon.KurekState.SPEAKING
    return thread, result


def test_it_waits_for_a_reply_to_finish(monkeypatch, tmp_path) -> None:
    engine, _notes = _engine(monkeypatch, tmp_path)
    engine.state = daemon.KurekState.SPEAKING
    engine._speaker_lock.acquire()
    thread, result = _announce(engine, "CPU high")
    try:
        time.sleep(0.3)
        assert engine.tts.calls == []
        engine.state = daemon.KurekState.IDLE
    finally:
        engine._speaker_lock.release()
    assert _wait_for(lambda: ("enqueue", "CPU high") in engine.tts.calls)
    engine.tts.is_playing = False
    thread.join(2)
    assert result == {"ok": True}
    assert engine.tts.calls == [("enqueue", "CPU high")]
    assert engine.state == daemon.KurekState.IDLE


def test_it_waits_while_the_owner_records(monkeypatch, tmp_path) -> None:
    engine, _notes = _engine(monkeypatch, tmp_path)
    engine.is_recording = True
    thread, result = _announce(engine, "CPU high")
    time.sleep(0.3)
    assert engine.tts.calls == []
    engine.is_recording = False
    assert _wait_for(lambda: ("enqueue", "CPU high") in engine.tts.calls)
    engine.tts.is_playing = False
    thread.join(2)
    assert result == {"ok": True}


def test_without_a_quiet_moment_it_becomes_a_notification(monkeypatch, tmp_path) -> None:
    engine, notes = _engine(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "ANNOUNCE_MAX_WAIT_S", 0.5)
    engine._speaker_lock.acquire()
    try:
        thread, result = _announce(engine, "CPU high")
        thread.join(2)
    finally:
        engine._speaker_lock.release()
    assert result == {"ok": False}
    assert engine.tts.calls == []
    assert notes == ["Not said aloud, Kurek was busy: CPU high"]


def test_a_new_prompt_stops_it_at_once(monkeypatch, tmp_path) -> None:
    engine, _notes = _engine(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "stream_deepseek_sentences", lambda **_k: iter([
        {"type": "sentence", "sentence": "Hi there.", "full_content": "Hi there."},
        {"type": "done", "full_content": "Hi there."},
    ]))
    thread, result = _playing(engine, "CPU high")
    turn = threading.Thread(target=engine.handle_text_query, args=("hi",))
    turn.start()
    try:
        assert _wait_for(lambda: ("enqueue", "Hi there.") in engine.tts.calls)
        calls = engine.tts.calls
        assert "stop" in calls
        assert calls.index("stop") < calls.index(("enqueue", "Hi there."))
        thread.join(1)
        assert not thread.is_alive()
    finally:
        engine.tts.is_playing = False
        turn.join(2)
    assert not turn.is_alive()
    assert result == {"ok": True}
    assert engine.history[-1]["content"] == "Hi there."


def test_a_click_stops_it(monkeypatch, tmp_path) -> None:
    engine, _notes = _engine(monkeypatch, tmp_path)
    thread, _result = _playing(engine, "CPU high")
    engine.toggle()
    assert "stop" in engine.tts.calls
    assert engine.state == daemon.KurekState.IDLE
    thread.join(1)
    assert not thread.is_alive()


def test_a_recording_that_starts_stops_it(monkeypatch, tmp_path) -> None:
    engine, _notes = _engine(monkeypatch, tmp_path)
    thread, _result = _playing(engine, "CPU high")
    engine.is_recording = True
    try:
        assert _wait_for(lambda: "stop" in engine.tts.calls, timeout=0.5)
        thread.join(1)
        assert not thread.is_alive()
    finally:
        engine.is_recording = False


def test_a_resource_alert_waits_and_does_not_block_the_watcher(monkeypatch, tmp_path) -> None:
    engine, _notes = _engine(monkeypatch, tmp_path)
    engine.state = daemon.KurekState.SPEAKING
    engine._speaker_lock.acquire()
    try:
        started = time.monotonic()
        _GLOBAL_WATCHER.alert_callback("RAM high")
        assert time.monotonic() - started < 0.2
        time.sleep(0.1)
        assert engine.tts.calls == []
        engine.state = daemon.KurekState.IDLE
    finally:
        engine._speaker_lock.release()
    assert _wait_for(lambda: ("enqueue", "RAM high") in engine.tts.calls)
    engine.tts.is_playing = False


def test_the_bridge_reaches_the_daemon(monkeypatch, tmp_path) -> None:
    assert tts_mod.announce("x") is False
    heard: list[str] = []
    monkeypatch.setattr(tts_mod, "_announcer", lambda text: heard.append(text) or True)
    assert tts_mod.announce("x") is True
    assert heard == ["x"]
    engine, _notes = _engine(monkeypatch, tmp_path)
    assert tts_mod._announcer == engine.announce


def _no_own_player(monkeypatch) -> list[str]:
    heard: list[str] = []
    monkeypatch.setattr(tts_mod, "_announcer", lambda text: heard.append(text) or True)
    monkeypatch.setattr(tts_mod, "create_tts_player", lambda *_a, **_k: pytest.fail("built its own player"))
    return heard


def test_a_finished_job_is_announced_through_the_daemon(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # its daily-log line lands in the temp home
    heard = _no_own_player(monkeypatch)
    process_sentinel._notify_job_complete("cargo", "2m 3s", 1234)
    assert heard == ["Your cargo job just finished in 2m 3s."]


def test_screen_watch_comments_go_through_the_daemon(monkeypatch) -> None:
    heard = _no_own_player(monkeypatch)
    monkeypatch.setattr(sv, "_WATCH_RUNNING", False)
    monkeypatch.setattr(sv, "_LAST_IMAGE_HASH", None)
    sv._watch_loop("errors")
    assert heard == ["Watching your screen now for errors. I will chime in when I notice something.",
                     "I've stopped watching the screen."]

    heard.clear()
    monkeypatch.setattr(sv, "_WATCH_RUNNING", True)
    monkeypatch.setattr(sv, "capture_screen_jpeg", lambda *_a, **_k: (b"x", 5000))
    monkeypatch.setattr(sv, "query_vision", lambda *_a, **_k: "obs")

    def comment(**_kwargs):
        sv._WATCH_RUNNING = False
        return "Nice code."

    monkeypatch.setattr("core.llm_client.query_deepseek", comment)
    sv._watch_loop("errors")
    assert "Nice code." in heard


def test_an_announcement_is_appended_never_replacing(monkeypatch, played) -> None:  # noqa: F811
    monkeypatch.setattr(core.mpv_sink, "get_mpv_sink", lambda: _Sink())
    player = tts_mod.TTSPlayer(_SynthEngine())
    player.begin_stream()
    player.enqueue("One.")
    assert player.wait_stream(5)
    player.enqueue("Notice.")
    assert player.wait_stream(5)
    assert played == [(b"One.", False), (b"Notice.", True)]


class _RaceTTS(_AnnounceTTS):
    """announce() reads is_playing last in its idle checks, holding the speaker lock: a new prompt arrives
    exactly then, and its barge-in (cancel, epoch, stop) runs before announce() goes on."""

    def __init__(self, engine, prompts: list) -> None:
        self.engine, self.prompts, self.fired = engine, prompts, False
        super().__init__()

    @property
    def is_playing(self) -> bool:
        if not self.fired and threading.current_thread().name == "kurek-announce":
            self.fired = True
            prompt = threading.Thread(target=self.engine.handle_text_query, args=("hi",), daemon=True)
            self.prompts.append(prompt)
            prompt.start()
            assert _wait_for(lambda: "stop" in self.calls, 1)  # its barge-in ran
            return False
        return self._playing

    @is_playing.setter
    def is_playing(self, value: bool) -> None:
        self._playing = value


def test_a_prompt_during_the_idle_check_still_stops_it(monkeypatch, tmp_path) -> None:
    engine, _notes = _engine(monkeypatch, tmp_path)
    prompts: list[threading.Thread] = []
    engine.tts = _RaceTTS(engine, prompts)
    monkeypatch.setattr(daemon, "stream_deepseek_sentences", lambda **_k: iter([
        {"type": "sentence", "sentence": "Hi there.", "full_content": "Hi there."},
        {"type": "done", "full_content": "Hi there."},
    ]))
    ends_alone = threading.Timer(2.0, lambda: setattr(engine.tts, "_playing", False))  # unless stopped
    ends_alone.start()
    thread, result = _announce(engine, "CPU high")
    try:
        assert _wait_for(lambda: ("enqueue", "Hi there.") in engine.tts.calls, 1.0)
        calls = engine.tts.calls
        after = calls[calls.index(("enqueue", "CPU high")) + 1:]
        assert "stop" in after and after.index("stop") < after.index(("enqueue", "Hi there."))
        thread.join(1)
        assert result == {"ok": True}
    finally:
        engine.tts.is_playing = False
        ends_alone.cancel()
        thread.join(3)
        for prompt in prompts:
            prompt.join(3)
