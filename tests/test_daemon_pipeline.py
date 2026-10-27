"""Offline tests for the daemon's VAD, memory helpers, TTS queue and tool loop."""
from __future__ import annotations

import os
import stat
import threading
import time

import pytest

import kurek_daemon as daemon
from core import tts as tts_mod
from tests.test_stream_errors import _make_lite_engine


# --- VAD / silence watchdog ---------------------------------------------------

def _run_vad(rms_values):
    noise_floor, chunks, has_speech = 0.022, 0, False
    sounds = []
    for rms in rms_values:
        noise_floor, chunks, has_speech, sound_now = daemon.vad_update(noise_floor, chunks, has_speech, rms)
        sounds.append(sound_now)
    return has_speech, sounds


def test_vad_quiet_room_never_detects_speech():
    has_speech, sounds = _run_vad([0.01] * 50)
    assert not has_speech
    assert not any(sounds)


def test_vad_needs_two_loud_chunks():
    has_speech, sounds = _run_vad([0.01, 0.2])
    assert not has_speech
    has_speech, sounds = _run_vad([0.01, 0.2, 0.2])
    assert has_speech
    assert sounds[-1] is True


def test_vad_soft_speech_after_onset_keeps_sound_alive():
    # After speech onset, a level above the softer silence threshold still counts.
    # The floor adapts during onset (≈0.071 after two 0.2 chunks), so the
    # silence threshold is ≈0.107 and the speech threshold ≈0.142.
    has_speech, sounds = _run_vad([0.2, 0.2, 0.12])
    assert has_speech
    assert sounds[-1] is True
    has_speech, sounds = _run_vad([0.2, 0.2, 0.05])
    assert sounds[-1] is False


def test_silence_stop_reasons():
    start = 1000.0
    assert daemon.silence_stop_reason(start + 1, True, start + 0.5, start) is None
    assert daemon.silence_stop_reason(start + 1 + daemon.SILENCE_DURATION, True, start + 0.5, start) == "silence"
    assert daemon.silence_stop_reason(start + daemon.INITIAL_SILENCE_TIMEOUT + 0.1, False, start, start) == "no_speech"
    # Continuous speech is still capped.
    now = start + daemon.MAX_RECORD_SECONDS + 0.1
    assert daemon.silence_stop_reason(now, True, now, start) == "max_duration"


# --- memory helpers -----------------------------------------------------------

def test_read_capped_keeps_head_and_tail(tmp_path):
    f = tmp_path / "MEMORY.md"
    f.write_text("HEAD" + "x" * 10_000 + "TAIL", encoding="utf-8")
    out = daemon.read_capped(f, limit=300)
    assert out.startswith("HEAD")
    assert out.endswith("TAIL")
    assert "omitted" in out
    assert len(out) < 400


def test_read_capped_warns_once_when_truncating(tmp_path, capsys):
    f = tmp_path / "m.md"
    f.write_text("x" * 500, encoding="utf-8")
    daemon.read_capped(f, limit=300)
    daemon.read_capped(f, limit=300)
    out = capsys.readouterr().out
    assert out.count("WARNING") == 1 and str(f) in out


def test_read_capped_small_file_unchanged(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("  short\n", encoding="utf-8")
    assert daemon.read_capped(f) == "short"


def test_history_save_is_atomic_and_private(monkeypatch, tmp_path):
    engine = _make_lite_engine(monkeypatch)
    f = tmp_path / "sub" / "hist.json"
    engine.history_file = f
    engine.history = [{"role": "user", "content": "1"}]
    engine._save_history()
    engine.history.append({"role": "assistant", "content": "2"})
    engine._save_history()
    assert '"2"' in f.read_text()
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert not list(f.parent.glob(".*.tmp"))


# --- TTS queue ----------------------------------------------------------------

class _SynthEngine:
    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.synth_calls: list[str] = []
        self.spoken: list[str] = []

    def synthesize(self, text: str) -> bytes:
        time.sleep(self.delay)
        self.synth_calls.append(text)
        return text.encode()

    def speak(self, text: str, append: bool = False) -> None:
        self.spoken.append(text)


@pytest.fixture
def played(monkeypatch):
    calls: list[tuple[bytes, bool]] = []
    monkeypatch.setattr(tts_mod, "_play_audio_bytes", lambda b, append=False: calls.append((b, append)))
    return calls


def test_tts_queue_plays_in_order_first_replaces(played):
    player = tts_mod.TTSPlayer(_SynthEngine())
    player.begin_stream()
    for s in ("One.", "Two.", "Three."):
        player.enqueue(s)
    assert player.wait_stream(timeout=5)
    assert played == [(b"One.", False), (b"Two.", True), (b"Three.", True)]


def test_tts_enqueue_does_not_block_caller(played):
    player = tts_mod.TTSPlayer(_SynthEngine(delay=0.2))
    t0 = time.monotonic()
    player.enqueue("Slow one.")
    player.enqueue("Slow two.")
    assert time.monotonic() - t0 < 0.1
    assert player.wait_stream(timeout=5)
    assert len(played) == 2


def test_tts_synthesizes_once_for_phone_and_speaker(played):
    engine = _SynthEngine()
    player = tts_mod.TTSPlayer(engine)
    got: list[bytes | None] = []
    player.enqueue("Hello.", play_local=True, on_audio=got.append)
    assert player.wait_stream(timeout=5)
    assert engine.synth_calls == ["Hello."]
    assert got == [b"Hello."]
    assert played == [(b"Hello.", False)]


def test_tts_muted_remote_only_sends_audio(played):
    player = tts_mod.TTSPlayer(_SynthEngine())
    got: list = []
    player.enqueue("Quiet.", play_local=False, on_audio=got.append)
    assert player.wait_stream(timeout=5)
    assert got == [b"Quiet."]
    assert played == []


def test_tts_stop_drops_queued_sentences(played, monkeypatch):
    monkeypatch.setattr(tts_mod.sd, "stop", lambda: None)
    gate = threading.Event()

    class _Blocking(_SynthEngine):
        def synthesize(self, text):
            gate.wait(5)
            return super().synthesize(text)

    player = tts_mod.TTSPlayer(_Blocking())
    for s in ("A.", "B.", "C."):
        player.enqueue(s)
    time.sleep(0.05)
    player.stop()
    gate.set()
    assert player.wait_stream(timeout=5)
    # The in-flight sentence was invalidated by stop(); nothing plays.
    assert played == []


def test_tts_falls_back_to_engine_speak_without_bytes(played):
    class _NoSynth:
        def __init__(self):
            self.spoken = []

        def speak(self, text, append=False):
            self.spoken.append(text)

    engine = _NoSynth()
    player = tts_mod.TTSPlayer(engine)
    player.enqueue("Robot voice.")
    assert player.wait_stream(timeout=5)
    assert engine.spoken == ["Robot voice."]
    assert played == []


# --- tool loop ------------------------------------------------------------------

def _tool_call_event(name: str, args: str = "{}", idx: int = 0):
    return {
        "type": "tool_calls",
        "content": "",
        "reasoning_content": "thinking",
        "tool_calls": [{"id": f"call_{idx}", "type": "function", "function": {"name": name, "arguments": args}}],
    }


def test_tool_loop_runs_tool_then_speaks_answer(monkeypatch, tmp_path):
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"
    engine.openai_tools = [{"type": "function", "function": {"name": "weather_report"}}]
    ran: list = []
    engine.actions = type("A", (), {"run": lambda self, n, a: ran.append((n, a)) or "Sunny, 20C"})()
    calls: list[dict] = []

    def fake_stream(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            yield _tool_call_event("weather_report", '{"city": "Ljubljana"}')
            return
        yield {"type": "sentence", "sentence": "It is sunny.", "full_content": "It is sunny."}
        yield {"type": "done", "full_content": "It is sunny."}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    reply = engine.handle_text_query("weather?")
    assert reply == "It is sunny."
    assert ran == [("weather_report", {"city": "Ljubljana"})]
    # The tool turn is sent back with reasoning_content and the tool result.
    second = calls[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["reasoning_content"] == "thinking"
    assert second[-1] == {"role": "tool", "tool_call_id": "call_0", "content": "Sunny, 20C"}
    assert "It is sunny." in engine.tts.spoken
    assert engine.state == daemon.KurekState.IDLE
    assert engine.history[-1] == {"role": "assistant", "content": "It is sunny."}


def test_tool_loop_last_turn_keeps_tools_but_forbids_calls(monkeypatch, tmp_path):
    # Haiku thinking blocks are bound to the tool list, so the last round keeps the
    # tools and ends the loop with tool_choice="none" instead of dropping them.
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"
    engine.openai_tools = [{"type": "function", "function": {"name": "noop"}}]
    engine.actions = type("A", (), {"run": lambda self, n, a: "ok"})()
    calls: list[dict] = []

    def fake_stream(**kwargs):
        calls.append(kwargs)
        if kwargs.get("tools") and kwargs.get("tool_choice") != "none":
            yield _tool_call_event("noop", idx=len(calls))
            return
        yield {"type": "sentence", "sentence": "Done looping.", "full_content": "Done looping."}
        yield {"type": "done", "full_content": "Done looping."}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    reply = engine.handle_text_query("loop forever")
    assert len(calls) == 15
    assert calls[-1]["tools"] == engine.openai_tools
    assert calls[-1]["tool_choice"] == "none"
    assert all(c["tool_choice"] is None for c in calls[:-1])
    assert reply == "Done looping."


def test_remote_stream_audio_emits_sentence_before_done(monkeypatch, tmp_path):
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"

    def fake_stream(**_kwargs):
        yield {"type": "sentence", "sentence": "Hi there.", "full_content": "Hi there."}
        yield {"type": "done", "full_content": "Hi there."}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    events: list[dict] = []
    engine.handle_text_query("hi", callback=events.append, mute_speaker=True, remote=True, stream_audio=True)
    types = [e["type"] for e in events]
    assert types == ["sentence", "done"]
    assert events[0]["sentence"] == "Hi there."
    # Muted remote request: nothing played on the PC speaker.
    assert engine.tts.spoken == []


def test_brain_key_problem_follows_the_active_brain(monkeypatch):
    monkeypatch.setattr(daemon, "get_deepseek_key", lambda: None)
    monkeypatch.setattr(daemon, "get_anthropic_key", lambda: None)

    monkeypatch.delenv("KUREK_LLM", raising=False)
    assert "DEEPSEEK_API_KEY" in daemon.brain_key_problem()

    monkeypatch.setenv("KUREK_LLM", "haiku")
    assert "ANTHROPIC_API_KEY" in daemon.brain_key_problem()

    monkeypatch.setattr(daemon, "get_anthropic_key", lambda: "sk-test-not-real")
    assert daemon.brain_key_problem() is None


def _ask_and_capture(monkeypatch, tmp_path, rules_toml: str | None):
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"
    rules = tmp_path / "rules.toml"
    if rules_toml is not None:
        rules.write_text(rules_toml, encoding="utf-8")
    monkeypatch.setattr("core.rules.RULES_PATH", rules)
    calls: list[dict] = []

    def fake_stream(**kwargs):
        calls.append(kwargs)
        yield {"type": "sentence", "sentence": "Hi.", "full_content": "Hi."}
        yield {"type": "done", "full_content": "Hi."}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    engine.handle_text_query("hello")
    return engine, calls[0]["messages"]


def test_pinned_rule_follows_the_user_message_but_not_the_history(monkeypatch, tmp_path):
    engine, messages = _ask_and_capture(
        monkeypatch, tmp_path, '[[rule]]\nid = "brief"\ntext = "Two sentences at most."\npin = true\n')
    last = messages[-1]
    assert last["role"] == "user" and last["content"].startswith("hello\n\n[PINNED USER RULES")
    assert last["content"].endswith("- [brief] Two sentences at most.")
    assert engine.history[-2] == {"role": "user", "content": "hello"}


def test_no_pinned_rules_leaves_the_user_message_unchanged(monkeypatch, tmp_path):
    _, messages = _ask_and_capture(monkeypatch, tmp_path, '[[rule]]\nid = "a"\ntext = "x"\n')
    assert messages[-1] == {"role": "user", "content": "hello"}
