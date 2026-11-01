"""A spoken "done" is checked against what the tools did this turn; phase 1 only logs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import kurek_daemon as daemon
from core import claim_gate, confirm
from tests.test_daemon_pipeline import _tool_call_event
from tests.test_stream_errors import _make_lite_engine

PENDING = "[CONFIRMATION_PENDING] A confirmation is waiting for: Move a.txt to Trash."


def _entry(tool: str, outcome: str = "ok", **args) -> dict:
    return {"tool": tool, "args": args, "outcome": outcome}


@pytest.mark.parametrize("result,outcome", [
    ("Created file: notes.txt", "ok"),
    ("Done.", "ok"),
    (PENDING, "pending_confirmation"),
    ("There is already a confirmation waiting. Ask the user to answer that one first.", "pending_confirmation"),
    ("TIMED OUT: tool 'run' did not finish within 120s.", "timed_out"),
    ("Access denied: /etc/passwd", "failed"),
    ("File not found: a.txt", "failed"),
    ("Tool 'send_message' failed: boom", "failed"),
    ("Tool 'run' is disabled for remote prompts (set KUREK_REMOTE_ALLOW_EXEC=1 to allow).", "failed"),
    ("Refused: rules.toml is edited only by the user.", "failed"),
    ("", "ok"),
])
def test_classify(result, outcome):
    assert claim_gate.classify(result) == outcome


@pytest.mark.parametrize("sentence,ledger,expected", [
    # claim with no backing tool call
    ("I deleted the file.", [], [("DELETED", "no_tool_call")]),
    ("I sent the message to Marko.", [], [("SENT", "no_tool_call")]),
    ("Saved it to your notes folder.", [], [("CREATED", "no_tool_call")]),
    # the delete is parked behind the confirmation: never done in-turn
    ("Deleted a.txt.", [_entry("file_controller", "pending_confirmation", action="delete")],
     [("DELETED", "pending_confirmation")]),
    # a failed or timed-out tool does not back the claim
    ("Message sent.", [_entry("send_message", "failed")], [("SENT", "failed")]),
    ("I created the file.", [_entry("file_controller", "timed_out", action="create_file")],
     [("CREATED", "timed_out")]),
    ("Thank you, I deleted it.", [], [("DELETED", "no_tool_call")]),
    # "written" as an adjective is not a claim; as a perfect tense it is
    ("He issued a written message as Speaker.", [], []),
    ("I have written the file.", [], [("CREATED", "no_tool_call")]),
    # a hedge or negation after the claim does not excuse it
    ("Saved it, no problems.", [], [("CREATED", "no_tool_call")]),
    ("Deleted it, and I would do it again.", [], [("DELETED", "no_tool_call")]),
    # a read, a list or a recall does not back a claim of doing something
    ("Deleted it.", [_entry("run", "ok", action="read")], [("DELETED", "no_tool_call")]),
    ("Sent it.", [_entry("run", "ok", action="list")], [("SENT", "no_tool_call")]),
    ("Saved that.", [_entry("manage_memory", "ok", action="recall")], [("CREATED", "no_tool_call")]),
    ("I created it.", [_entry("code_helper", "ok", action="explain")], [("CREATED", "no_tool_call")]),
    # an exec with no action given still backs it (run defaults to exec)
    ("Saved the output.", [_entry("run", "ok")], []),
    # the tool is right but the action is not (a read does not back "saved")
    ("I saved the report.", [_entry("file_controller", "ok", action="read")], [("CREATED", "no_tool_call")]),
    # backed claims pass
    ("I saved the report.", [_entry("file_controller", "ok", action="write")], []),
    ("Message sent.", [_entry("send_message", "ok")], []),
    ("Created the folder.", [_entry("file_controller", "ok", action="mkdir")], []),
    ("Saved that to memory.", [_entry("manage_memory", "ok", action="remember")], []),
    # not claims: hypothetical, question, plan, refusal, the user's own action
    ("If you save it there, it would be deleted.", [], []),
    ("Do you want me to delete it?", [], []),
    ("I'll send it now.", [], []),
    ("I could not delete the file.", [], []),
    ("I didn't save anything.", [], []),
    ("Nothing was sent.", [], []),
    ("The message you sent yesterday was short.", [], []),
    ("You've already deleted it.", [], []),
    ("You just saved it yourself.", [], []),
    ("The weather is sunny.", [], []),
    ("", [], []),
])
def test_evaluate(sentence, ledger, expected):
    hits = [(h["class"], h["reason"]) for h in claim_gate.evaluate(sentence, ledger)]
    assert hits == expected


def test_observe_logs_one_json_line_per_hit_and_keeps_the_sentence(tmp_path):
    log = Path(claim_gate.os.environ["KUREK_CLAIM_LOG"])
    ledger = [_entry("file_controller", "pending_confirmation", action="delete")]
    hits = claim_gate.observe("Deleted a.txt.", ledger)
    assert [h["class"] for h in hits] == ["DELETED"]
    row = json.loads(log.read_text().strip())
    assert row["sentence"] == "Deleted a.txt." and row["class"] == "DELETED"
    assert row["reason"] == "pending_confirmation"
    assert row["ledger"] == [{"tool": "file_controller", "outcome": "pending_confirmation"}]
    assert oct(log.stat().st_mode & 0o777) == "0o600"


def test_observe_off_logs_nothing(monkeypatch):
    monkeypatch.setenv("KUREK_CLAIM_GATE", "off")
    assert claim_gate.observe("I deleted the file.", []) == []
    assert not Path(claim_gate.os.environ["KUREK_CLAIM_LOG"]).exists()


def _delete_turn(monkeypatch, tmp_path, final_sentence: str):
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"
    engine.openai_tools = [{"type": "function", "function": {"name": "file_controller"}}]
    engine.actions = type("A", (), {"run": lambda self, n, a: PENDING})()
    calls: list[dict] = []

    def fake_stream(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            yield _tool_call_event("file_controller", '{"action": "delete", "path": "a.txt"}')
            return
        yield {"type": "sentence", "sentence": final_sentence, "full_content": final_sentence}
        yield {"type": "done", "full_content": final_sentence}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    return engine, engine.handle_text_query("delete a.txt")


def test_false_deleted_claim_is_logged_but_still_spoken(monkeypatch, tmp_path):
    engine, reply = _delete_turn(monkeypatch, tmp_path, "Deleted a.txt.")
    assert reply == "Deleted a.txt." and "Deleted a.txt." in engine.tts.spoken
    rows = [json.loads(r) for r in Path(claim_gate.os.environ["KUREK_CLAIM_LOG"]).read_text().splitlines()]
    assert [(r["class"], r["reason"]) for r in rows] == [("DELETED", "pending_confirmation")]


def test_honest_pending_sentence_logs_nothing(monkeypatch, tmp_path):
    engine, _ = _delete_turn(monkeypatch, tmp_path, "Waiting for your yes before I move it to the trash.")
    assert not Path(claim_gate.os.environ["KUREK_CLAIM_LOG"]).exists()
    assert engine.tts.spoken


def test_gate_failure_never_stops_speech(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(claim_gate, "observe", boom)
    engine, reply = _delete_turn(monkeypatch, tmp_path, "Deleted a.txt.")
    assert reply == "Deleted a.txt." and "Deleted a.txt." in engine.tts.spoken


def test_pending_confirmation_marker_matches_what_confirm_returns(monkeypatch):
    confirm.bind(lambda t, d: None, lambda: None)
    monkeypatch.setattr(confirm, "_pending", None)
    try:
        result = confirm.request("k", "Move a.txt to Trash", "/tmp/a.txt", lambda: "done")
        assert claim_gate.classify(result) == "pending_confirmation"
    finally:
        confirm.bind(None, None)
        confirm._pending = None
