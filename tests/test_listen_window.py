"""Offline tests for the adaptive listening window and the toggle contract."""
from __future__ import annotations

import threading

import pytest

import kurek_daemon as daemon
from kurek_daemon import KurekEngine, KurekState


def test_base_window_with_no_speech_yet() -> None:
    assert daemon.effective_silence_window(1000.0, 1000.0) == pytest.approx(daemon.SILENCE_DURATION)


def test_window_grows_with_how_long_you_have_been_speaking() -> None:
    short = daemon.effective_silence_window(1002.0, 1000.0)   # 2 s spoken
    long = daemon.effective_silence_window(1005.0, 1000.0)    # 5 s spoken
    assert short > daemon.SILENCE_DURATION
    assert long > short


def test_window_growth_is_capped() -> None:
    huge = daemon.effective_silence_window(10000.0, 1000.0)
    assert huge == pytest.approx(daemon.SILENCE_DURATION + daemon.SILENCE_MAX_EXTRA)


def test_long_thought_survives_a_pause_that_would_otherwise_submit() -> None:
    start = 1000.0
    # 40 s of speech, then a 3.5 s pause: open, because the window grew.
    last_sound = start + 40.0
    assert daemon.silence_stop_reason(last_sound + 3.5, True, last_sound, start) is None
    # The same 3.5 s pause after a two-word answer still submits.
    brief = start + 1.0
    assert daemon.silence_stop_reason(brief + 3.5, True, brief, start) == "silence"


def test_hard_ceiling_is_three_minutes() -> None:
    assert daemon.MAX_RECORD_SECONDS == pytest.approx(180.0)
    start = 1000.0
    now = start + daemon.MAX_RECORD_SECONDS + 0.1
    assert daemon.silence_stop_reason(now, True, now, start) == "max_duration"


def test_auto_submit_is_on_by_default(monkeypatch) -> None:
    monkeypatch.delenv("KUREK_AUTO_SUBMIT", raising=False)
    assert daemon.auto_submit_enabled() is True


def test_auto_submit_off_requires_a_click(monkeypatch) -> None:
    monkeypatch.setenv("KUREK_AUTO_SUBMIT", "off")
    start = 1000.0
    last_sound = start + 20.0
    # A minute of silence is not a reason to submit.
    assert daemon.silence_stop_reason(last_sound + 60.0, True, last_sound, start) is None
    # Nor is never having spoken: manual mode does not guess that the mic is idle.
    assert daemon.silence_stop_reason(start + 60.0, False, start, start) is None
    # The ceiling still bounds a forgotten session.
    assert daemon.silence_stop_reason(start + 181.0, True, start + 175.0, start) == "max_duration"


def test_quiet_desk_mic_is_detected() -> None:
    """Ambient 0.0008 with speech at 0.01 used to be invisible to the 0.045 gate."""
    noise, chunks, has_speech = 0.004, 0, False
    sounds = []
    for rms in [0.0008] * 5 + [0.01] * 4:
        noise, chunks, has_speech, sound_now = daemon.vad_update(noise, chunks, has_speech, rms)
        sounds.append(sound_now)
    assert has_speech is True
    assert sounds[-1] is True


def test_loud_room_still_needs_speech_above_its_own_noise() -> None:
    noise, chunks, has_speech = 0.004, 0, False
    for i, rms in enumerate([0.02] * 40):  # steady fan, no speech
        noise, chunks, has_speech, _ = daemon.vad_update(
            noise, chunks, has_speech, rms, warmup=i < 8
        )
    assert has_speech is False
    assert noise > 0.015


def test_max_window_for_a_long_question() -> None:
    start = 1000.0
    window = daemon.effective_silence_window(start + 30.0, start)
    assert window == pytest.approx(daemon.SILENCE_DURATION + daemon.SILENCE_MAX_EXTRA)
    # 30 s into a question a 4 s pause is fine; the base window alone would submit.
    assert daemon.silence_stop_reason(start + 34.0, True, start + 30.0, start) is None
    assert daemon.silence_stop_reason(start + 34.0 + daemon.SILENCE_MAX_EXTRA, True, start + 30.0, start) == "silence"


class _ToggleStub:
    def __init__(self, state: KurekState):
        self.state = state
        self.state_lock = threading.Lock()
        self._cancel_requested = False
        self.started = 0
        self.stopped = 0

    def start_listening(self) -> None:
        self.started += 1

    def stop_listening_and_process(self) -> None:
        self.stopped += 1

    def set_state(self, state: KurekState) -> None:
        self.state = state


def test_middle_click_starts_when_idle() -> None:
    stub = _ToggleStub(KurekState.IDLE)
    KurekEngine.toggle(stub)
    assert (stub.started, stub.stopped) == (1, 0)


def test_second_middle_click_stops_and_submits() -> None:
    """Press to start, press again to finish — the manual path is already wired."""
    stub = _ToggleStub(KurekState.LISTENING)
    KurekEngine.toggle(stub)
    assert (stub.started, stub.stopped) == (0, 1)
