"""KUREK_TIMING covers the whole turn: STT, the DeepSeek request, tools and the TTS worker (offline)."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import numpy as np

import core.tts as tts_mod
from core import timing
from core.llm_client import stream_deepseek
from tests.test_core_llm_stream import _Resp, _setup, _tok
from tests.test_daemon_pipeline import _SynthEngine, _tool_call_event
from tests.test_stream_errors import _make_lite_engine
from tests.test_turn_endings import _ANSWER, _engine, _two_rounds

_TAG = "[Kurek Timing]"


def test_marks_only_when_enabled(monkeypatch, capsys) -> None:
    qt = timing.begin_query(t0=time.perf_counter() - 1.0)
    try:
        timing.mark("stt_done")
        assert qt.marks == {}
    finally:
        timing.end_query()
    assert _TAG not in capsys.readouterr().out

    monkeypatch.setenv("KUREK_TIMING", "1")
    qt = timing.begin_query(t0=time.perf_counter() - 1.0)
    try:
        timing.mark("stt_done")
        assert qt.marks["stt_done"] >= 1000.0
    finally:
        timing.end_query()
    out = capsys.readouterr().out
    assert f"{_TAG} stt_done: " in out
    assert f"{_TAG} summary: " in out
    assert timing.current_timing() is None


def test_deepseek_request_marks(monkeypatch) -> None:
    reasoning = b"data: " + json.dumps({"choices": [{"delta": {"reasoning_content": "hmm"}}]}).encode()
    _setup(monkeypatch, [_Resp(lines=[reasoning, _tok("Hi there."), b"data: [DONE]"])])
    monkeypatch.setenv("KUREK_TIMING", "1")
    qt = timing.begin_query()
    try:
        events = list(stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    finally:
        timing.end_query()
    assert events == [
        {"type": "token", "token": "Hi there."},
        {"type": "done", "full_content": "Hi there.", "reasoning_content": "hmm"},
    ]
    assert {"deepseek_request_sent", "deepseek_headers", "deepseek_first_reasoning"} <= set(qt.marks)


def test_tts_worker_marks_into_the_turn_clock(monkeypatch) -> None:
    monkeypatch.setenv("KUREK_TIMING", "1")
    player = tts_mod.TTSPlayer(_SynthEngine())
    qt = timing.begin_query()
    try:
        player.enqueue("Hello there.", play_local=False, on_audio=lambda _a: None)
        assert player.wait_stream(timeout=5)
    finally:
        timing.end_query()
    assert {"first_speak_chunk", "tts_synth_done"} <= set(qt.marks)
    assert qt.marks["first_speak_chunk"] <= qt.marks["tts_synth_done"]


def test_tts_worker_clock_does_not_go_stale(monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(tts_mod, "_play_audio_bytes", lambda b, append=False: seen.append(timing.current_timing()))
    player = tts_mod.TTSPlayer(_SynthEngine())
    qt = timing.begin_query()
    try:
        player.enqueue("One.", play_local=True)
        assert player.wait_stream(timeout=5)
    finally:
        timing.end_query()
    player.enqueue("Two.", play_local=True)
    assert player.wait_stream(timeout=5)
    assert seen[0] is qt and seen[1] is None


def test_voice_turn_clock_starts_at_the_stop_click(monkeypatch) -> None:
    engine = _make_lite_engine(monkeypatch)
    engine.audio_buffer = [np.zeros(160, dtype=np.float32)]
    engine.stt = SimpleNamespace(transcribe=lambda audio, sample_rate: "hello")
    seen: list = []
    engine.handle_text_query = lambda text: seen.append((text, timing.current_timing()))
    monkeypatch.setenv("KUREK_TIMING", "1")
    t0 = time.perf_counter() - 2.0
    engine._timed_process_recorded_audio(t0)
    assert seen[0][0] == "hello"
    assert seen[0][1].t0 == t0
    assert seen[0][1].marks["stt_done"] >= 2000.0
    assert timing.current_timing() is None


def test_stop_click_passes_its_time(monkeypatch) -> None:
    engine = _make_lite_engine(monkeypatch)
    engine.is_recording, engine.record_stream = True, None
    engine.audio_buffer = [np.zeros(160, dtype=np.float32)]
    got: list = []
    done = threading.Event()
    engine._timed_process_recorded_audio = lambda t0: (got.append(t0), done.set())
    before = time.perf_counter()
    engine.stop_listening_and_process()
    assert done.wait(5)
    assert before <= got[0] <= time.perf_counter()


def test_turn_marks_only_when_enabled(monkeypatch, tmp_path, capsys) -> None:
    engine = _engine(monkeypatch, tmp_path)
    read = _tool_call_event("file_controller", '{"action": "read", "path": "a.md"}')
    _two_rounds(monkeypatch, read, _ANSWER)
    engine.handle_text_query("read a.md", mute_speaker=True)
    assert _TAG not in capsys.readouterr().out

    monkeypatch.setenv("KUREK_TIMING", "1")
    _two_rounds(monkeypatch, read, _ANSWER)
    engine.handle_text_query("read a.md", mute_speaker=True)
    out = capsys.readouterr().out
    for name in ("trigger_receipt", "tool_start:file_controller", "tool_end:file_controller", "summary"):
        assert f"{_TAG} {name}:" in out


def test_a_mark_landing_during_the_summary_does_not_break_it(monkeypatch, capsys) -> None:
    monkeypatch.setenv("KUREK_TIMING", "1")
    qt = timing.QueryTiming()

    class _MarkedMidSummary(float):
        def __format__(self, spec):
            qt.marks["tts_synth_done"] = 2.0  # the TTS worker marks while the summary iterates
            return super().__format__(spec)

    qt.marks = {"trigger_receipt": _MarkedMidSummary(0.0), "stt_done": 1.0}
    qt.summary()
    assert "tts_synth_done" in qt.marks
    assert f"{_TAG} summary: trigger_receipt=0.0ms, stt_done=1.0ms" in capsys.readouterr().out
