"""C3: a sentence no cloud voice could speak is reported instead of skipped silently; the local
fallback still tries it and the phone still gets its text."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.mpv_sink
import core.tts as tts_mod
import kurek_daemon as daemon
from tests.test_stream_errors import _make_lite_engine


@pytest.fixture(autouse=True)
def _fake_sink(monkeypatch):
    sink = SimpleNamespace(is_playing=lambda: False, stop=lambda: None, position=lambda: None)
    monkeypatch.setattr(core.mpv_sink, "get_mpv_sink", lambda: sink)
    return sink


class _Local:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str, append: bool = False) -> None:
        self.spoken.append(text)


class _Cloudless:
    """Every cloud voice failed: synthesize() gives no bytes; the local fallback speaks."""

    def __init__(self) -> None:
        self._fallback = _Local()

    def synthesize(self, text: str):
        return None

    def speak(self, text: str) -> None:
        raise AssertionError("the cloud chain must not run again")


def test_a_sentence_no_cloud_voice_could_speak_is_reported() -> None:
    engine = _Cloudless()
    player = tts_mod.TTSPlayer(engine)
    notes: list[str] = []
    player.on_unvoiced = notes.append
    player.enqueue("Hello there.")
    assert player.wait_stream(5)
    assert notes == ["Hello there."]
    assert engine._fallback.spoken == ["Hello there."]


def test_a_phone_only_sentence_is_not_reported() -> None:
    player = tts_mod.TTSPlayer(_Cloudless())
    notes: list[str] = []
    player.on_unvoiced = notes.append
    got: list = []
    player.enqueue("Quiet.", play_local=False, on_audio=got.append)
    assert player.wait_stream(5)
    assert got == [None]
    assert notes == []


def test_an_engine_without_cloud_voices_is_not_reported() -> None:
    player = tts_mod.TTSPlayer(_Local())
    notes: list[str] = []
    player.on_unvoiced = notes.append
    player.enqueue("Robot voice.")
    assert player.wait_stream(5)
    assert notes == []


def test_the_daemon_shows_an_unvoiced_sentence(monkeypatch) -> None:
    engine = _make_lite_engine(monkeypatch)
    notes: list[str] = []
    monkeypatch.setattr(engine, "_notify_failure", notes.append)
    engine.tts.on_unvoiced("abc")
    assert notes == ["Could not voice: abc"]


def test_the_phone_still_gets_the_text(monkeypatch, tmp_path) -> None:
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"
    engine.tts = tts_mod.TTSPlayer(_Cloudless())
    monkeypatch.setattr(daemon, "stream_deepseek_sentences", lambda **_k: iter([
        {"type": "sentence", "sentence": "Hello.", "full_content": "Hello."},
        {"type": "done", "full_content": "Hello."},
    ]))
    events: list[dict] = []
    engine.handle_text_query("hi", callback=events.append, mute_speaker=True, stream_audio=True)
    assert {"type": "sentence", "sentence": "Hello."} in events
    assert events.index({"type": "sentence", "sentence": "Hello."}) < len(events) - 1
    assert events[-1]["type"] == "done"
