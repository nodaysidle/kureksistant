"""C5: a copied cancelled-turn marker is dropped; the rest of the answer is still spoken and saved."""
from __future__ import annotations

import kurek_daemon as daemon
from tests.test_turn_endings import _engine

M = daemon.CANCELLED_REPLY


def _stream(monkeypatch, *events):
    monkeypatch.setattr(daemon, "stream_deepseek_sentences", lambda **_k: iter(events))


def test_marker_sentence_is_dropped_and_the_answer_is_spoken(monkeypatch, tmp_path) -> None:
    engine = _engine(monkeypatch, tmp_path)
    _stream(
        monkeypatch,
        {"type": "sentence", "sentence": M},
        {"type": "sentence", "sentence": "The file is saved."},
        {"type": "done", "full_content": f"{M} The file is saved."},
    )
    events: list[dict] = []
    engine.handle_text_query("save the file", callback=events.append)
    assert engine.tts.spoken == ["The file is saved."]
    assert engine.history[-1]["role"] == "assistant"
    assert engine.history[-1]["content"] == "The file is saved."
    assert not any(M in e.get("sentence", "") for e in events)


def test_marker_inside_a_sentence_keeps_the_rest(monkeypatch, tmp_path) -> None:
    engine = _engine(monkeypatch, tmp_path)
    _stream(
        monkeypatch,
        {"type": "sentence", "sentence": f"{M} Here it is."},
        {"type": "done", "full_content": f"{M} Here it is."},
    )
    engine.handle_text_query("show it")
    assert engine.tts.spoken == ["Here it is."]
