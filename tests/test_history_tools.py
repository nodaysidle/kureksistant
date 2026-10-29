"""Stored assistant turns tell later turns which tools they really ran."""

from __future__ import annotations

import kurek_daemon as daemon


def test_assistant_turn_lists_its_tools():
    msg = daemon.history_message(
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


def test_turn_without_tools_says_none():
    msg = daemon.history_message({"role": "assistant", "content": "Hi.", "tools": []})
    assert msg["content"].endswith("recorded by the daemon: none.)")


def test_old_turns_and_user_turns_are_unchanged():
    assert daemon.history_message({"role": "assistant", "content": "Hi."}) == {
        "role": "assistant",
        "content": "Hi.",
    }
    assert daemon.history_message({"role": "user", "content": "q", "tools": []}) == {
        "role": "user",
        "content": "q",
    }
