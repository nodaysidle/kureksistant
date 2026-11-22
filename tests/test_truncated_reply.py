"""C4: an answer cut by the length limit (or another finish_reason than stop) says so, on the PC and
on the phone; what the model is sent does not change, on that turn or later ones (A4)."""
from __future__ import annotations

import json

import core.llm_client as llm_client
import kurek_daemon as daemon
from tests.test_stream_errors import _FakeResponse
from tests.test_turn_endings import _engine


def _line(content: str, finish_reason: str | None = None) -> bytes:
    choice: dict = {"delta": {"content": content}}
    if finish_reason:
        choice["finish_reason"] = finish_reason
    return f"data: {json.dumps({'choices': [choice]})}".encode()


def _last_event(monkeypatch, resp) -> dict:
    monkeypatch.setattr("memory.config_manager.get_deepseek_key", lambda: "sk-test-not-real")
    monkeypatch.setattr(llm_client.requests, "post", lambda *_a, **_k: resp)
    events = list(llm_client.stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    assert events[-1]["type"] == "done"
    return events[-1]


def test_length_limit_marks_the_answer_truncated(monkeypatch) -> None:
    lines = [_line("Part one. "), _line("Part two", "length"), b"data: [DONE]"]
    assert _last_event(monkeypatch, _FakeResponse(lines=lines))["truncated"] == "length"


def test_a_finished_answer_is_not_marked(monkeypatch) -> None:
    lines = [_line("Part one. "), _line("Part two", "stop"), b"data: [DONE]"]
    assert "truncated" not in _last_event(monkeypatch, _FakeResponse(lines=lines))


def test_other_finish_reasons_are_passed_on(monkeypatch) -> None:
    lines = [_line("Part one", "insufficient_system_resource"), b"data: [DONE]"]
    assert _last_event(monkeypatch, _FakeResponse(lines=lines))["truncated"] == "insufficient_system_resource"


def test_sentence_stream_passes_the_mark_on(monkeypatch) -> None:
    def fake_stream(**_kwargs):
        yield {"type": "token", "token": "A. "}
        yield {"type": "token", "token": "B"}
        yield {"type": "done", "full_content": "A. B", "truncated": "length"}

    monkeypatch.setattr(llm_client, "stream_deepseek", fake_stream)
    events = list(llm_client.stream_deepseek_sentences(messages=[{"role": "user", "content": "hi"}]))
    assert events[-1]["type"] == "done"
    assert events[-1]["truncated"] == "length"


def _turn(monkeypatch, tmp_path, done: dict):
    engine = _engine(monkeypatch, tmp_path)
    seen: list[dict] = []

    def fake_stream(**kwargs):
        seen.append({"messages": json.loads(json.dumps(kwargs["messages"], default=str)), "tools": kwargs["tools"]})
        yield {"type": "sentence", "sentence": "Part one.", "full_content": "Part one."}
        yield done

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    monkeypatch.setattr(daemon, "datetime", _FixedClock)
    events: list[dict] = []
    engine.handle_text_query("tell me everything", callback=events.append)
    return engine, events, seen


class _FixedClock(daemon.datetime):
    @classmethod
    def now(cls, tz=None):
        return daemon.datetime(2026, 11, 22, 9, 0, 0)


def test_daemon_says_a_cut_answer_is_incomplete(monkeypatch, tmp_path) -> None:
    notice = daemon.TRUNCATED_NOTICES["length"]
    engine, events, cut_seen = _turn(
        monkeypatch, tmp_path, {"type": "done", "full_content": "Part one.", "truncated": "length"}
    )
    assert engine.tts.spoken == ["Part one.", notice]
    assert events[-1] == {"type": "done", "reply": "Part one. " + notice, "truncated": "length"}
    assert engine.history[-1]["content"] == "Part one."  # the model never sees the notice: it would copy it
    later: list[list] = []

    def next_turn(**kwargs):
        later.append(kwargs["messages"])
        yield {"type": "sentence", "sentence": "Sure.", "full_content": "Sure."}
        yield {"type": "done", "full_content": "Sure."}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", next_turn)
    engine.handle_text_query("and the rest?")
    sent = json.dumps(later, default=str)
    assert "Part one." in sent
    assert not any(n in sent for n in daemon.TRUNCATED_NOTICES.values())

    engine, events, whole_seen = _turn(monkeypatch, tmp_path, {"type": "done", "full_content": "Part one."})
    assert engine.tts.spoken == ["Part one."]
    assert events[-1] == {"type": "done", "reply": "Part one."}
    assert engine.history[-1]["content"] == "Part one."
    assert cut_seen == whole_seen  # the model's input is the same either way
