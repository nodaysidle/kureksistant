"""Live voice prints and sends tool arguments redacted and cut; the tool itself gets them whole."""
import json
from types import SimpleNamespace

from core import live_voice

KEY = "sk-" + "Zq7" * 10


def test_live_tool_call_arguments_are_redacted(capsys):
    phone, xai, ran = [], [], []
    args = {"action": "search", "query": f"token={KEY} " + "q" * 1000}
    ev = {"name": "codebase", "arguments": json.dumps(args), "call_id": "c1"}
    live_voice._answer_tool_call(
        ev, SimpleNamespace(send=xai.append), SimpleNamespace(send=phone.append),
        lambda n, a: ran.append((n, a)) or "ok",
    )
    out = capsys.readouterr().out
    assert "[Live Voice] ⚙️ codebase(" in out
    assert KEY not in out
    assert max(len(line) for line in out.splitlines()) < 600
    assert KEY not in "".join(phone)
    [tool_event] = [json.loads(m) for m in phone]
    assert tool_event["type"] == "kurek.tool"
    assert tool_event["arguments"]["action"] == "search"  # Kurek Remote shows the action
    assert len(tool_event["arguments"]["query"]) <= 201
    assert ran == [("codebase", args)]  # the tool runs on the real arguments


def test_live_redaction_fails_closed(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "actions.run", None)  # any import of it now raises
    assert live_voice._redacted("x" * 50, 500) == "[redaction unavailable]"
