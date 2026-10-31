"""DeepSeek caches the longest shared prompt prefix between requests, so nothing
that changes from turn to turn may sit before the end of the system message."""
from __future__ import annotations

import json
from datetime import datetime

import kurek_daemon as daemon
from tests.test_stream_errors import _make_lite_engine

TOOLS = [{"type": "function", "function": {
    "name": "web_search", "description": "Search the web.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
}}]


def _capture(engine, monkeypatch, when: datetime) -> tuple[str, list]:
    captured: dict = {}

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return when

    def fake_stream(**kw):
        captured.update(kw)
        yield {"type": "done", "full_content": "ok"}

    monkeypatch.setattr(daemon, "datetime", _Clock)
    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    engine._handle_text_query_inner("What's the capital of Australia?")
    return captured["messages"][0]["content"], captured["tools"]


def test_system_prompt_is_stable_until_the_time_section(monkeypatch, tmp_path):
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"
    engine.openai_tools = TOOLS

    sys1, tools1 = _capture(engine, monkeypatch, datetime(2026, 10, 31, 9, 0))
    engine.history = []
    sys2, tools2 = _capture(engine, monkeypatch, datetime(2026, 10, 31, 9, 1, 1))

    marker = "[CURRENT TIME]"
    assert sys1.count(marker) == 1 and sys2.count(marker) == 1
    head1, tail1 = sys1.split(marker)
    head2, tail2 = sys2.split(marker)
    assert head1 == head2, "everything before the time must be byte-identical"
    assert "09:00 AM" in tail1 and "09:01 AM" in tail2
    assert "\n\n[" not in tail1, "the time section must be the last one"
    assert "09:00 AM" not in head1
    assert json.dumps(tools1) == json.dumps(tools2)
