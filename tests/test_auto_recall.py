"""Prompts that point at the past get the matching daily-log turns attached."""
from __future__ import annotations

from datetime import date

import pytest

import kurek_daemon as daemon
from core import auto_recall
from tests.test_stream_errors import _make_lite_engine

TODAY = date(2026, 10, 31)  # a Saturday


@pytest.mark.parametrize("prompt,days", [
    ("what did we talk about yesterday?", ["2026-10-30"]),
    ("kaj sva se včeraj pogovarjala?", ["2026-10-30"]),
    ("cosa ho detto ieri?", ["2026-10-30"]),
    ("and the day before yesterday?", ["2026-10-29"]),
    ("what did I say 2 days ago", ["2026-10-29"]),
    ("what did I say two days ago", ["2026-10-29"]),
    ("kaj sem rekel pred dvema dnevoma", ["2026-10-29"]),
    ("cosa ho detto 3 giorni fa", ["2026-10-28"]),
    ("what did I work on last week", [f"2026-10-{d}" for d in range(30, 23, -1)]),
    ("what did I tell you on Tuesday", ["2026-10-27"]),
    ("what did I say last Saturday", ["2026-10-24"]),
    ("what did I say about the phone app?", []),
])
def test_window(prompt, days):
    assert auto_recall.window(prompt, TODAY) == days


@pytest.mark.parametrize("prompt", [
    "what's the weather", "open firefox", "what's on this week", "che tempo fa oggi",
])
def test_no_window_for_ordinary_prompts(prompt):
    assert auto_recall.window(prompt, TODAY) is None


@pytest.fixture
def logs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "memory").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (home / "memory" / "2026-10-30.md").write_text(
        "\n### [10:00:00] turn\n**User:** the phone app crashed when I rotate it\n**Kurek:** noted\n"
        "\n### [11:00:00] turn\n**User:** order pizza for tonight\n**Kurek:** done\n"
    )
    (home / "memory" / "2026-10-20.md").write_text(
        "\n### [09:00:00] turn\n**User:** the phone app needs a dark theme\n**Kurek:** ok\n"
    )
    return home


def test_dated_question_with_subject(logs):
    block = auto_recall.recall_block("what did I say yesterday about the phone app?", TODAY)
    assert block.startswith(auto_recall.HEADER)
    assert "[Said on 2026-10-30]" in block and "rotate" in block
    assert "dark theme" not in block, "only yesterday was asked about"


def test_dated_question_without_subject_gets_that_days_turns(logs):
    block = auto_recall.recall_block("what did we talk about yesterday?", TODAY)
    assert "pizza" in block and "rotate" in block


def test_undated_question_searches_every_day(logs):
    block = auto_recall.recall_block("what did I say about the phone app?", TODAY)
    assert "dark theme" in block and "rotate" in block


def test_subject_not_in_logs_adds_nothing(logs):
    assert auto_recall.recall_block("what were the F1 results last week?", TODAY) is None


def test_ordinary_prompt_adds_nothing(logs):
    assert auto_recall.recall_block("open firefox", TODAY) is None


def test_block_is_capped(logs, monkeypatch):
    long_turns = "".join(f"\n### [10:{i:02d}:00] turn\n**User:** phone {'x' * 600}\n" for i in range(20))
    (logs / "memory" / "2026-10-30.md").write_text(long_turns)
    block = auto_recall.recall_block("what did I say yesterday about the phone?", TODAY)
    assert len(block) <= auto_recall.MAX_CHARS


def test_daemon_attaches_recall_to_the_message_not_the_history(logs, monkeypatch, tmp_path):
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"
    seen: list[list[dict]] = []

    def fake_stream(**kw):
        seen.append(kw["messages"])
        yield {"type": "done", "full_content": "You said it crashed when rotated."}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    monkeypatch.setattr(auto_recall, "date", type("D", (date,), {"today": staticmethod(lambda: TODAY)}))
    engine.handle_text_query("what did I say yesterday about the phone app?")

    user_msg = seen[0][-1]["content"]
    assert auto_recall.HEADER in user_msg and "rotate" in user_msg
    assert auto_recall.HEADER not in seen[0][0]["content"], "system prompt stays cache-stable"
    assert engine.history[-2]["content"] == "what did I say yesterday about the phone app?"
