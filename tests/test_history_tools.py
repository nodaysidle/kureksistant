"""Stored assistant turns tell later turns which tools they really ran."""

from __future__ import annotations

import kurek_daemon as daemon
from core import context
from memory import ledger as memory_ledger


def test_assistant_turn_lists_its_tools():
    msg = context.history_message(
        {
            "role": "assistant",
            "content": "Released Oct 27.",
            "tools": ['web_search({"query": "x"})'],
        }
    )
    assert msg == {
        "role": "assistant",
        "content": 'Released Oct 27.\n\n(Tool calls in that turn, recorded by the daemon: web_search({"query": "x"}).)',
    }


def test_turn_without_tools_has_no_note():
    msg = context.history_message({"role": "assistant", "content": "Hi.", "tools": []})
    assert msg["content"] == "Hi."


def test_old_turns_and_user_turns_are_unchanged():
    assert context.history_message({"role": "assistant", "content": "Hi."}) == {
        "role": "assistant",
        "content": "Hi.",
    }
    assert context.history_message({"role": "user", "content": "q", "tools": []}) == {
        "role": "user",
        "content": "q",
    }


def test_copied_tool_note_is_never_spoken(monkeypatch, tmp_path):
    from tests.test_daemon_pipeline import _make_lite_engine

    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"
    full = "It is sunny. (Tool calls in that turn, recorded by the daemon: none.) More."

    def fake_stream(**_kwargs):
        yield {"type": "sentence", "sentence": "It is sunny.", "full_content": full}
        yield {"type": "sentence", "sentence": "(Tool calls in that turn, recorded by the daemon: none.)", "full_content": full}
        yield {"type": "sentence", "sentence": "More.", "full_content": full}
        yield {"type": "done", "full_content": full}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    assert engine.handle_text_query("weather?") == "It is sunny."
    assert engine.tts.spoken == ["It is sunny."]


def test_turns_from_before_an_outside_memory_correction_are_not_sent(monkeypatch, tmp_path):
    from tests.test_daemon_pipeline import _make_lite_engine

    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"
    engine.history = [
        {"role": "user", "content": "I use Velocitty."},
        {"role": "assistant", "content": "Noted.", "tools": []},
        {"role": "user", "content": "hi", "ts": "2026-10-30T12:00:00"},
        {"role": "assistant", "content": "Hello.", "tools": [], "ts": "2026-10-30T12:00:00"},
    ]
    monkeypatch.setattr(memory_ledger, "outside_correction_ts", lambda: "2026-10-30T11:00:00")
    seen: list[list[dict]] = []

    def fake_stream(**kwargs):
        seen.append(kwargs["messages"])
        yield {"type": "done", "full_content": "Ghostty."}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    engine.handle_text_query("Which terminal do I use?")
    sent = [m["content"] for m in seen[0][1:]]
    assert "I use Velocitty." not in sent and "hi" in sent
    assert all("ts" in t for t in engine.history[-2:])
