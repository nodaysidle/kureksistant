"""How a turn ends: unreadable tool arguments, brain failures, empty answers and cancels."""
from types import SimpleNamespace

import kurek_daemon as daemon
from tests.test_daemon_pipeline import _tool_call_event
from tests.test_stream_errors import _make_lite_engine


def _engine(monkeypatch, tmp_path, run=None):
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"
    engine.openai_tools = [{"type": "function", "function": {"name": "file_controller"}}]
    engine.actions = SimpleNamespace(run=run or (lambda n, a: "ok"))
    # No desktop toasts from tests.
    monkeypatch.setattr(engine, "_notify_failure", lambda _msg: None)
    return engine


def _no_ts(entry):
    return {k: v for k, v in entry.items() if k != "ts"}


def _two_rounds(monkeypatch, first_event, second_events):
    calls: list[dict] = []

    def fake_stream(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            yield first_event
            return
        yield from second_events

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    return calls


_ANSWER = [
    {"type": "sentence", "sentence": "I could not save it.", "full_content": "I could not save it."},
    {"type": "done", "full_content": "I could not save it."},
]


def test_invalid_json_arguments_are_not_dispatched(monkeypatch, tmp_path):
    ran: list = []
    engine = _engine(monkeypatch, tmp_path, run=lambda n, a: ran.append((n, a)) or "ok")
    calls = _two_rounds(
        monkeypatch, _tool_call_event("file_controller", '{"action": "create_file", "path": '), _ANSWER
    )
    engine.handle_text_query("save notes")
    assert ran == []
    tool_msg = calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_0"
    assert tool_msg["content"].startswith("Error: the arguments for file_controller were not valid JSON")
    assert engine.history[-1]["tools"] == []


def test_json_that_is_not_an_object_is_not_dispatched(monkeypatch, tmp_path):
    ran: list = []
    engine = _engine(monkeypatch, tmp_path, run=lambda n, a: ran.append((n, a)) or "ok")
    _two_rounds(monkeypatch, _tool_call_event("file_controller", "[1, 2]"), _ANSWER)
    engine.handle_text_query("save notes")
    assert ran == []


def test_empty_arguments_still_mean_no_arguments(monkeypatch, tmp_path):
    ran: list = []
    engine = _engine(monkeypatch, tmp_path, run=lambda n, a: ran.append((n, a)) or "ok")
    _two_rounds(monkeypatch, _tool_call_event("system_monitor", ""), _ANSWER)
    engine.handle_text_query("how is the system")
    assert ran == [("system_monitor", {})]


def test_brain_failure_ends_the_phone_stream(monkeypatch, tmp_path):
    engine = _engine(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "stream_deepseek_sentences",
                        lambda **_k: iter([{"type": "error", "error": "HTTP 503"}]))
    events: list = []
    engine.handle_text_query("hi", callback=events.append, mute_speaker=True)
    msg = f"I couldn't reach {daemon.brain_name()} right now."
    assert events == [{"type": "error", "error": msg}, {"type": "done", "reply": msg}]
    assert engine.history == []


def test_empty_answer_ends_the_phone_stream(monkeypatch, tmp_path):
    engine = _engine(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "stream_deepseek_sentences",
                        lambda **_k: iter([{"type": "done", "full_content": ""}]))
    events: list = []
    engine.handle_text_query("hi", callback=events.append, mute_speaker=True)
    msg = f"I didn't get a response from {daemon.brain_name()}."
    assert events == [{"type": "error", "error": msg}, {"type": "done", "reply": msg}]
    assert engine.history == []


def test_cancelled_turn_ends_the_phone_stream_silently(monkeypatch, tmp_path):
    engine = _engine(monkeypatch, tmp_path)

    def cancelled_stream(**_k):
        engine._cancel_requested = True
        yield {"type": "sentence", "sentence": "Too late.", "full_content": "Too late."}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", cancelled_stream)
    events: list = []
    engine.handle_text_query("hi", callback=events.append)
    assert events == [
        {"type": "error", "error": daemon.CANCELLED_REPLY},
        {"type": "done", "reply": daemon.CANCELLED_REPLY},
    ]
    assert engine.tts.spoken == []
    assert engine.history == []


def test_brain_failure_after_tools_says_what_they_did_and_keeps_the_turn(monkeypatch, tmp_path):
    engine = _engine(monkeypatch, tmp_path, run=lambda n, a: "File created: /tmp/n/notes.md")
    calls = _two_rounds(
        monkeypatch,
        _tool_call_event("file_controller", '{"action": "create_file", "path": "notes.md"}'),
        [{"type": "error", "error": "HTTP 503"}],
    )
    events: list = []
    reply = engine.handle_text_query("make notes.md", callback=events.append)
    expected = (f"Tools this turn: file controller create file ok. "
                f"Then I couldn't reach {daemon.brain_name()}, so I could not finish.")
    assert reply == expected
    assert engine.tts.spoken == [expected]
    assert [e["type"] for e in events] == ["tool", "sentence", "done"]
    assert events[-1]["reply"] == expected
    assert [_no_ts(h) for h in engine.history] == [
        {"role": "user", "content": "make notes.md"},
        {"role": "assistant", "content": expected,
         "tools": ['file_controller({"action": "create_file", "path": "notes.md"})']},
    ]
    assert len(calls) == 2


def test_cancelled_turn_keeps_its_tools_and_starts_no_more(monkeypatch, tmp_path):
    ran: list = []

    def run(name, args):
        ran.append(args["path"])
        engine._cancel_requested = True  # the user barged in mid-tool
        return "File created: /tmp/n/a.md"

    engine = _engine(monkeypatch, tmp_path, run=run)
    two_calls = _tool_call_event("file_controller", '{"action": "create_file", "path": "a.md"}')
    two_calls["tool_calls"] += _tool_call_event(
        "file_controller", '{"action": "create_file", "path": "b.md"}', idx=1
    )["tool_calls"]
    calls: list = []

    def fake_stream(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            yield two_calls

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    events: list = []
    engine.handle_text_query("make a.md and b.md", callback=events.append)
    assert ran == ["a.md"]
    assert len(calls) == 1
    assert [e["type"] for e in events] == ["tool", "error", "done"]
    assert engine.tts.spoken == []
    assert [_no_ts(h) for h in engine.history] == [
        {"role": "user", "content": "make a.md and b.md"},
        {"role": "assistant", "content": daemon.CANCELLED_REPLY,
         "tools": ['file_controller({"action": "create_file", "path": "a.md"})']},
    ]


def test_refused_call_then_brain_failure_is_a_plain_brain_failure(monkeypatch, tmp_path):
    ran: list = []
    engine = _engine(monkeypatch, tmp_path, run=lambda n, a: ran.append((n, a)) or "ok")
    _two_rounds(
        monkeypatch,
        _tool_call_event("file_controller", '{"action": "create_file", "path": '),
        [{"type": "error", "error": "HTTP 503"}],
    )
    events: list = []
    engine.handle_text_query("save notes", callback=events.append)
    msg = f"I couldn't reach {daemon.brain_name()} right now."
    assert ran == []
    assert engine.tts.spoken == [msg]
    assert events[-2:] == [{"type": "error", "error": msg}, {"type": "done", "reply": msg}]
    assert not any("failed" in str(e.get("sentence", "")) + str(e.get("reply", "")) for e in events)
    assert engine.history == []


def test_cancelled_reply_text_is_neutral():
    assert daemon.CANCELLED_REPLY == "Interrupted before replying."


def test_a_copied_cancelled_reply_is_never_spoken(monkeypatch, tmp_path):
    engine = _engine(monkeypatch, tmp_path)
    copied = f"{daemon.CANCELLED_REPLY} The file is saved."

    def stream(**_k):
        yield {"type": "sentence", "sentence": daemon.CANCELLED_REPLY, "full_content": daemon.CANCELLED_REPLY}
        yield {"type": "sentence", "sentence": "The file is saved.", "full_content": copied}
        yield {"type": "done", "full_content": copied}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", stream)
    events: list = []
    engine.handle_text_query("is it saved?", callback=events.append)
    assert not any(daemon.CANCELLED_REPLY in s for s in engine.tts.spoken)
    assert not any(daemon.CANCELLED_REPLY in e.get("sentence", "") for e in events)
