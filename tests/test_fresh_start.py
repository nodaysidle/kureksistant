"""'Fresh start' clears only the rolling chat history, without calling the LLM."""
from __future__ import annotations

import json
import threading

import pytest

import kurek_daemon as daemon


@pytest.mark.parametrize("text", [
    "fresh start", "Fresh start.", "Kurek, fresh start", "Kurek fresh start!",
    "new conversation", "Kurek, new conversation",
])
def test_fresh_start_phrases(text):
    assert daemon.is_fresh_start(text)


@pytest.mark.parametrize("text", [
    "", "start", "what is a fresh start in rust", "give the project a fresh start",
    "fresh start for the build please", "conversation",
])
def test_other_prompts_are_not_fresh_start(text):
    assert not daemon.is_fresh_start(text)


def test_fresh_start_clears_history_only(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.confirm, "is_pending", lambda: False)
    engine = object.__new__(daemon.KurekEngine)
    engine._history_lock = threading.Lock()
    engine._cancel_requested = False
    engine.history_file = tmp_path / "kurek_history.json"
    engine.history = [{"role": "user", "content": "I use Velocity"}]
    events = []

    reply = engine._handle_text_query_inner("Kurek, fresh start", callback=events.append, mute_speaker=True)

    assert engine.history == []
    assert json.loads(engine.history_file.read_text()) == []
    assert events[-1] == {"type": "done", "reply": reply}
