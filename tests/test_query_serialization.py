"""One turn at a time: a new prompt cancels the running one, and a cancelled
turn leaves no history and no stale speech behind."""
from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

import core.tts as tts_mod
import kurek_daemon as daemon
from memory import paths as memory_paths
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


def test_muted_query_runs_concurrently_during_local_tts_drain(engine, monkeypatch):
    """A mute_speaker=True query must not wait on or interrupt local TTS drain."""
    playing_started = threading.Event()
    finish_playback = threading.Event()
    state_history: list[str] = []

    class _DrainingTTS:
        def __init__(self):
            self.is_playing = False
            self.begin_calls = 0
            self.stop_calls = 0
            self.spoken: list[str] = []

        def begin_stream(self):
            self.begin_calls += 1

        def enqueue(self, text: str, play_local: bool = True, on_audio=None):
            if play_local:
                self.spoken.append(text)
                self.is_playing = True
                playing_started.set()
            if on_audio is not None:
                on_audio(b"remote-audio")

        def wait_stream(self, timeout: float = 60.0) -> bool:
            return True

        def synthesize(self, text: str):
            return b"remote-audio"

        def speak(self, text: str):
            self.spoken.append(text)

        def stop(self):
            self.stop_calls += 1
            self.is_playing = False
            finish_playback.set()

    tts = _DrainingTTS()
    engine.tts = tts
    orig_set_state = engine.set_state

    def track_state(new_state: str):
        state_history.append(new_state)
        orig_set_state(new_state)

    monkeypatch.setattr(engine, "set_state", track_state)

    calls = {"n": 0}

    def fake_stream(**_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {"type": "sentence", "sentence": "Local reply."}
            yield {"type": "done", "full_content": "Local reply."}
        else:
            yield {"type": "sentence", "sentence": "Remote reply."}
            yield {"type": "done", "full_content": "Remote reply."}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)

    def _run_local():
        engine.handle_text_query("local question")

    t_local = threading.Thread(target=_run_local)
    t_local.start()
    assert playing_started.wait(5)

    # Wait until Turn 1 has released _engine_lock and is strictly in TTS drain.
    deadline = time.monotonic() + 5
    while engine._engine_lock.locked() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not engine._engine_lock.locked()
    assert engine._speaker_lock.locked()
    assert engine.state == daemon.KurekState.SPEAKING

    events: list[dict] = []
    state_history.clear()
    remote_reply = engine.handle_text_query(
        "remote question",
        callback=events.append,
        mute_speaker=True,
        remote=True,
        stream_audio=True,
    )

    assert remote_reply == "Remote reply."
    assert [e["type"] for e in events] == ["sentence", "done"]
    assert tts.stop_calls == 0
    assert tts.begin_calls == 1
    assert tts.is_playing is True
    assert engine.state == daemon.KurekState.SPEAKING
    assert state_history == []

    # Let local TTS finish draining and verify clean return to IDLE.
    tts.is_playing = False
    finish_playback.set()
    t_local.join(5)
    assert not t_local.is_alive()
    assert not engine._speaker_lock.locked()
    assert engine.state == daemon.KurekState.IDLE


def test_set_state_idle_ignored_while_local_speaker_playing(engine, monkeypatch):
    monkeypatch.setattr(engine, "_notify_state", lambda _s: None)
    engine.state = daemon.KurekState.SPEAKING
    engine.tts.is_playing = True
    engine._cancel_requested = False
    assert engine._speaker_lock.acquire(timeout=1)
    try:
        engine.set_state(daemon.KurekState.IDLE)
        assert engine.state == daemon.KurekState.SPEAKING
    finally:
        engine._speaker_lock.release()

    # Toggle barge-in stops TTS and still transitions immediately to IDLE.
    engine.state = daemon.KurekState.SPEAKING
    engine.tts.is_playing = True
    engine._speaker_lock.acquire()
    try:
        engine.toggle()
        assert engine.state == daemon.KurekState.IDLE
        assert engine._cancel_requested is True
    finally:
        engine._speaker_lock.release()


def test_local_prompt_barges_in_on_draining_tts(engine, monkeypatch):
    playing_started = threading.Event()

    class _DrainingTTS:
        def __init__(self):
            self.is_playing = False
            self.stop_calls = 0
            self.spoken: list[str] = []

        def begin_stream(self):
            pass

        def enqueue(self, text: str, play_local: bool = True, on_audio=None):
            if play_local:
                self.spoken.append(text)
                if len(self.spoken) == 1:
                    self.is_playing = True
                    playing_started.set()

        def wait_stream(self, timeout: float = 60.0) -> bool:
            return True

        def synthesize(self, text: str):
            return None

        def speak(self, text: str):
            self.spoken.append(text)

        def stop(self):
            self.stop_calls += 1
            self.is_playing = False

    engine.tts = _DrainingTTS()
    calls = {"n": 0}

    def fake_stream(**_kw):
        calls["n"] += 1
        msg = "First." if calls["n"] == 1 else "Second."
        yield {"type": "sentence", "sentence": msg}
        yield {"type": "done", "full_content": msg}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)

    t1 = threading.Thread(target=engine.handle_text_query, args=("first",))
    t1.start()
    assert playing_started.wait(5)
    deadline = time.monotonic() + 5
    while engine._engine_lock.locked() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert engine._speaker_lock.locked()

    reply = engine.handle_text_query("second", mute_speaker=False)
    t1.join(5)
    assert not t1.is_alive()
    assert reply == "Second."
    assert engine.tts.stop_calls == 1
    assert engine.state == daemon.KurekState.IDLE


def test_muted_enqueue_preserves_local_stream_and_survives_begin_stream(played, monkeypatch):
    monkeypatch.setattr(tts_mod.sd, "stop", lambda: None)
    gate = threading.Event()
    player = tts_mod.TTSPlayer(_blocking_engine(gate))

    player.begin_stream()
    player.enqueue("Local one.", play_local=True)
    player.enqueue("Local two.", play_local=True)

    remote_audio: list[bytes | None] = []
    # Muted remote turn does NOT call begin_stream(), preserving local queue.
    player.enqueue("Remote one.", play_local=False, on_audio=remote_audio.append)
    gate.set()
    assert player.wait_stream(timeout=5)
    assert played == [(b"Local one.", False), (b"Local two.", True)]
    assert remote_audio == [b"Remote one."]

    # Now test that a local begin_stream() or stop() drops queued local items
    # without dropping queued muted remote-audio items.
    gate2 = threading.Event()
    player2 = tts_mod.TTSPlayer(_blocking_engine(gate2))
    played.clear()
    remote_audio2: list[bytes | None] = []
    player2.enqueue("Stale local.", play_local=True)
    player2.enqueue("Keep remote.", play_local=False, on_audio=remote_audio2.append)
    time.sleep(0.05)
    player2.stop()
    gate2.set()
    assert player2.wait_stream(timeout=5)
    assert played == []
    assert remote_audio2 == [b"Keep remote."]


def test_engine_uses_xdg_history_path_without_migrating(monkeypatch):
    called = {"migrated": False}
    monkeypatch.setattr(
        daemon,
        "migrate_legacy_runtime_files",
        lambda *_a, **_k: called.__setitem__("migrated", True) or {},
    )
    eng = _make_lite_engine(monkeypatch)
    assert eng.history_file == memory_paths.history_path()
    assert called["migrated"] is False


def test_system_prompt_apology_names_and_screen_first(engine, monkeypatch):
    seen: list[str] = []

    def fake_stream(**kw):
        seen.append(kw["messages"][0]["content"])
        yield {"type": "done", "full_content": "ok"}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    engine.handle_text_query("hello")
    sys_prompt = seen[0]
    assert "'Sorry, Boris.'" in sys_prompt
    assert "'Sorry, Master Alan.'" in sys_prompt
    assert "screen_vision(action='inspect')" in sys_prompt
    assert "this window" in sys_prompt
    assert "this error" in sys_prompt
    assert "on my screen" in sys_prompt
    assert "what am I looking at" in sys_prompt
