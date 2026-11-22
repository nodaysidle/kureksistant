"""Tool lines in the journal and the phone's tool event are redacted and cut; the model's copy is not."""
import json

import kurek_daemon as daemon
from tests.test_daemon_pipeline import _tool_call_event
from tests.test_parallel_tools import _round
from tests.test_turn_endings import _ANSWER, _engine, _two_rounds

KEY = "sk-" + "Zq7" * 10
RESULT = f"DEEPSEEK_API_KEY={KEY} " + "x" * 2000
RESULT_PREFIX = "[Kurek Tool Result] → "


def test_serial_tool_lines_are_redacted_and_cut(monkeypatch, tmp_path, capsys):
    engine = _engine(monkeypatch, tmp_path, run=lambda n, a: RESULT)
    args = json.dumps({"content": KEY, "action": "write", "path": "a.md"})
    calls = _two_rounds(monkeypatch, _tool_call_event("file_controller", args), _ANSWER)
    events = []
    engine.handle_text_query("save it", callback=events.append, mute_speaker=True)
    out = capsys.readouterr().out
    assert KEY not in out
    assert "[Kurek Tool Dispatch] ⚙️ file_controller(" in out
    result_lines = [ln for ln in out.splitlines() if ln.startswith(RESULT_PREFIX)]
    assert len(result_lines) == 1
    assert len(result_lines[0]) <= len(RESULT_PREFIX) + 501
    tool = next(e for e in events if e["type"] == "tool")
    assert KEY not in json.dumps(tool)
    assert len(tool["result"]) <= 101
    tool_msg = next(m for m in calls[1]["messages"] if m["role"] == "tool")
    assert tool_msg["content"] == RESULT  # the model gets the whole result
    assert KEY in engine.history[-1]["tools"][0]  # history the model reads back is unchanged


def test_refused_arguments_line_is_redacted(monkeypatch, tmp_path, capsys):
    engine = _engine(monkeypatch, tmp_path)
    _two_rounds(monkeypatch, _tool_call_event("file_controller", '{"content": "' + KEY + '"'), _ANSWER)
    engine.handle_text_query("save it", mute_speaker=True)
    out = capsys.readouterr().out
    assert KEY not in out
    assert "✋ file_controller" in out


def test_parallel_round_lines_are_redacted(monkeypatch, tmp_path, capsys):
    engine = _engine(monkeypatch, tmp_path, run=lambda n, a: f"found token={KEY}")
    calls = _two_rounds(
        monkeypatch, _round(("web_search", '{"query": "a"}'), ("web_search", '{"query": "b"}')), _ANSWER
    )
    events = []
    engine.handle_text_query("a and b", callback=events.append, mute_speaker=True)
    out = capsys.readouterr().out
    assert KEY not in out
    assert "⚡ 2 read-only calls at once" in out
    assert KEY not in json.dumps([e for e in events if e["type"] == "tool"])
    tool_msgs = [m for m in calls[1]["messages"] if m["role"] == "tool"]
    assert [m["content"] for m in tool_msgs] == [f"found token={KEY}"] * 2


def test_redacted_helper(monkeypatch):
    assert daemon._redacted("x" * 600, 500) == "x" * 500 + "…"
    assert daemon._redacted("short", 500) == "short"
    assert "Zq7" not in daemon._redacted("a" * 94 + " " + KEY, 100)  # redact before cutting
    fake = "made-up-" + "q" * 20
    monkeypatch.setenv("XAI_API_KEY", fake)
    assert "[REDACTED_XAI_API_KEY]" in daemon._redacted("v=" + fake, 500)
    assert daemon._redacted({"a": 1}, 50) == "{'a': 1}"


def test_redacted_is_bounded_on_long_results():
    import time
    start = time.monotonic()
    out = daemon._redacted("a" * 200_000, 500)
    assert time.monotonic() - start < 1
    assert out == "a" * 500 + "…"
    near_limit = "a" * 480 + " " + KEY + " " + "a" * 200_000  # the key crosses the 500 limit
    assert "Zq7" not in daemon._redacted(near_limit, 500)


_SILENT = [{"type": "done", "full_content": ""}]  # the model says nothing, also in the summary round


def test_a_reply_built_from_a_tool_result_is_redacted(monkeypatch, tmp_path, capsys):
    for result in (f"saved token={KEY}", f"error: rejected token={KEY}"):
        engine = _engine(monkeypatch, tmp_path, run=lambda n, a, r=result: r)
        _two_rounds(monkeypatch, _tool_call_event("file_controller", '{"action": "list"}'), _SILENT)
        events = []
        reply = engine.handle_text_query("save it", callback=events.append, mute_speaker=True)
        out = capsys.readouterr().out
        assert reply and KEY not in reply, result
        assert KEY not in engine.history[-1]["content"]
        assert KEY not in json.dumps([e for e in events if e["type"] == "done"])
        assert KEY not in out


def test_redaction_fails_closed_and_the_turn_completes(monkeypatch, tmp_path, capsys):
    import sys
    monkeypatch.setitem(sys.modules, "actions.run", None)  # any import of it now raises
    assert daemon._redacted("x" * 50, 500) == "[redaction unavailable]"
    assert daemon._redacted("x" * 50, 10) == "[redaction"
    engine = _engine(monkeypatch, tmp_path, run=lambda n, a: f"found token={KEY}")
    calls = _two_rounds(monkeypatch, _tool_call_event("file_controller", '{"action": "list"}'), _ANSWER)
    events = []
    reply = engine.handle_text_query("list it", callback=events.append, mute_speaker=True)
    out = capsys.readouterr().out
    assert reply == "I could not save it."
    assert KEY not in out
    assert "[Kurek Tool Result] → [redaction unavailable]" in out
    assert next(e for e in events if e["type"] == "tool")["result"] == "[redaction unavailable]"
    assert next(m for m in calls[1]["messages"] if m["role"] == "tool")["content"] == f"found token={KEY}"
