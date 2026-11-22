"""The extra "summary" round after tools logs one fixed line each time it runs."""
import kurek_daemon as daemon
from tests.test_daemon_pipeline import _tool_call_event
from tests.test_turn_endings import _engine

_MARKER = "[Kurek Summary Round]"


def _script(monkeypatch, *rounds):
    calls = []

    def fake_stream(**kw):
        calls.append(kw)
        yield from rounds[len(calls) - 1]

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    return calls


def test_summary_round_is_logged(monkeypatch, tmp_path, capsys):
    engine = _engine(monkeypatch, tmp_path)
    calls = _script(
        monkeypatch,
        [_tool_call_event("file_controller", '{"action": "read", "path": "a.md"}')],
        [{"type": "done", "full_content": ""}],
        [
            {"type": "sentence", "sentence": "It says hi.", "full_content": "It says hi."},
            {"type": "done", "full_content": "It says hi."},
        ],
    )
    reply = engine.handle_text_query("read a.md", mute_speaker=True)
    assert len(calls) == 3
    assert reply == "It says hi."
    assert capsys.readouterr().out.count(_MARKER) == 1


def test_no_summary_round_no_log(monkeypatch, tmp_path, capsys):
    engine = _engine(monkeypatch, tmp_path)
    calls = _script(
        monkeypatch,
        [_tool_call_event("file_controller", '{"action": "read", "path": "a.md"}')],
        [
            {"type": "sentence", "sentence": "It says hi.", "full_content": "It says hi."},
            {"type": "done", "full_content": "It says hi."},
        ],
    )
    engine.handle_text_query("read a.md", mute_speaker=True)
    assert len(calls) == 2
    assert _MARKER not in capsys.readouterr().out
