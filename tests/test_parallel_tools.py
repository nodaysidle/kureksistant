"""A round of read-only tool calls runs in parallel; anything else stays serial, in the original order."""
import threading
import time

import kurek_daemon as daemon
from tests.test_turn_endings import _ANSWER, _engine, _no_ts, _two_rounds


def _round(*calls):
    return {"type": "tool_calls", "content": "", "reasoning_content": "thinking", "tool_calls": [
        {"id": f"call_{i}", "type": "function", "function": {"name": n, "arguments": a}}
        for i, (n, a) in enumerate(calls)]}


def _tool_messages(call):
    return [m for m in call["messages"] if m["role"] == "tool"]


_A = ("web_search", '{"query": "a"}')
_B = ("web_search", '{"query": "b"}')


def test_parallel_round_keeps_the_call_order(monkeypatch, tmp_path):
    barrier = threading.Barrier(2, timeout=2)

    def run(_n, a):
        barrier.wait()  # both calls must be running at once, or this raises
        if a["query"] == "a":
            time.sleep(0.05)
        return f"result {a['query']}"

    engine = _engine(monkeypatch, tmp_path, run=run)
    calls = _two_rounds(monkeypatch, _round(_A, _B), _ANSWER)
    events: list = []
    engine.handle_text_query("a and b", callback=events.append, mute_speaker=True)
    tool_msgs = _tool_messages(calls[1])
    assert [m["tool_call_id"] for m in tool_msgs] == ["call_0", "call_1"]
    assert [m["content"] for m in tool_msgs] == ["result a", "result b"]
    assert engine.history[-1]["tools"] == ['web_search({"query": "a"})', 'web_search({"query": "b"})']
    assert [(e["type"], e.get("name")) for e in events][:2] == [("tool", "web_search"), ("tool", "web_search")]
    assert not [t for t in threading.enumerate() if t.name.startswith("kurek-tool")]  # the pool is shut down


def _recording_run(ran, threads):
    def run(n, _a):
        ran.append(n)
        threads.append(threading.current_thread().name)
        return "ok"
    return run


def test_mixed_round_stays_serial(monkeypatch, tmp_path):
    ran: list = []
    threads: list = []
    engine = _engine(monkeypatch, tmp_path, run=_recording_run(ran, threads))
    _two_rounds(monkeypatch, _round(_A, ("file_controller", '{"action": "create_file", "path": "x.md"}')), _ANSWER)
    engine.handle_text_query("a then x.md", mute_speaker=True)
    assert ran == ["web_search", "file_controller"]
    assert threads == [threading.current_thread().name] * 2


def test_one_call_stays_serial(monkeypatch, tmp_path):
    ran: list = []
    threads: list = []
    engine = _engine(monkeypatch, tmp_path, run=_recording_run(ran, threads))
    _two_rounds(monkeypatch, _round(_A), _ANSWER)
    engine.handle_text_query("a", mute_speaker=True)
    assert ran == ["web_search"]
    assert threads == [threading.current_thread().name]


def test_recall_with_remember_stays_serial(monkeypatch, tmp_path):
    ran: list = []
    threads: list = []
    engine = _engine(monkeypatch, tmp_path, run=_recording_run(ran, threads))
    _two_rounds(monkeypatch, _round(
        ("manage_memory", '{"action": "recall", "query": "phone"}'),
        ("manage_memory", '{"action": "remember", "key": "k", "value": "v"}'),
    ), _ANSWER)
    engine.handle_text_query("recall and remember", mute_speaker=True)
    assert ran == ["manage_memory", "manage_memory"]
    assert threads == [threading.current_thread().name] * 2


def test_unreadable_call_keeps_the_round_serial(monkeypatch, tmp_path):
    ran: list = []
    threads: list = []
    engine = _engine(monkeypatch, tmp_path, run=_recording_run(ran, threads))
    calls = _two_rounds(monkeypatch, _round(_A, ("web_search", '{"query": ')), _ANSWER)
    engine.handle_text_query("a and broken", mute_speaker=True)
    assert ran == ["web_search"]
    assert threads == [threading.current_thread().name]
    assert _tool_messages(calls[1])[1]["content"].startswith("Error: the arguments for web_search were not valid JSON")


def test_failing_parallel_call_keeps_the_other(monkeypatch, tmp_path):
    def run(_n, a):
        if a["query"] == "a":
            raise RuntimeError("boom")
        return "ok b"

    engine = _engine(monkeypatch, tmp_path, run=run)
    calls = _two_rounds(monkeypatch, _round(_A, _B), [{"type": "error", "error": "HTTP 503"}])
    reply = engine.handle_text_query("a and b", mute_speaker=True)
    assert reply == (f"Tools this turn: web search failed; web search ok. "
                     f"Then I couldn't reach {daemon.brain_name()}, so I could not finish.")
    assert [m["content"] for m in _tool_messages(calls[1])] == ["Error executing web_search: boom", "ok b"]


def test_cancel_during_the_round_records_every_call(monkeypatch, tmp_path):
    def run(_n, a):
        if a["query"] == "a":
            engine._cancel_requested = True  # the user barged in while the round ran
        return f"result {a['query']}"

    engine = _engine(monkeypatch, tmp_path, run=run)
    calls: list = []

    def fake_stream(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            yield _round(_A, _B)

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    events: list = []
    engine.handle_text_query("a and b", callback=events.append)
    assert len(calls) == 1
    assert [e["type"] for e in events] == ["tool", "tool", "error", "done"]
    assert engine.tts.spoken == []
    assert [_no_ts(h) for h in engine.history] == [
        {"role": "user", "content": "a and b"},
        {"role": "assistant", "content": daemon.CANCELLED_REPLY,
         "tools": ['web_search({"query": "a"})', 'web_search({"query": "b"})']},
    ]


def test_cancel_before_the_round_runs_nothing(monkeypatch, tmp_path):
    ran: list = []
    engine = _engine(monkeypatch, tmp_path, run=lambda n, a: ran.append(n) or "ok")
    engine._cancel_requested = True
    assert engine._run_parallel_round(_round(_A, _B)["tool_calls"], remote=False) == {}
    assert ran == []


def test_parallel_allowlist():
    safe = [
        ("web_search", {"query": "x"}),
        ("weather_report", {"city": "Oslo"}),
        ("manage_memory", {"action": "recall"}),
        ("vault_knowledge", {"action": "search"}),
        ("codebase", {"action": "read"}),
    ]
    unsafe = [
        ("manage_memory", {"action": "remember"}),
        ("manage_memory", {}),
        ("vault_knowledge", {"action": "add_inbox"}),
        ("vault_knowledge", {}),
        ("codebase", {"action": "index"}),
        ("codebase", {"action": "status"}),
        ("vault_knowledge", {"action": ["read"]}),
        ("file_controller", {"action": "read"}),
    ]
    assert all(daemon.parallel_safe(n, a) is True for n, a in safe)
    assert all(daemon.parallel_safe(n, a) is False for n, a in unsafe)
