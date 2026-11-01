"""The owner's dictation dictionary fixes what the microphone heard, and never breaks a command."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import kurek_daemon as daemon
from core import vocabulary
from tests.test_stream_errors import _make_lite_engine


def _dictionary(tmp_path: Path, monkeypatch, replacements: list[tuple[str, str]]) -> Path:
    path = tmp_path / "dictionary.json"
    path.write_text(json.dumps({
        "replacements": [{"id": str(i), "original": o, "with": w} for i, (o, w) in enumerate(replacements)],
        "vocabulary": ["Ghostty"],
    }), encoding="utf-8")
    monkeypatch.setenv("KUREK_VOCAB_FILE", str(path))
    return path


@pytest.mark.parametrize("original,target,kept", [
    ("no days idle", "nodaysidle", True),
    ("open router", "OpenRouter", True),
    ("ghosty", "Ghostty", True),
    ("clod code", "Claude Code", True),
    # command words, short words and symbol words must never be rewritten alone
    ("yes", "Yes please", False),
    ("delete", "DELETE", False),
    ("LDO", "Eldio", False),
    ("slash", "/", False),
    ("dash", "-", False),
    ("underscore", "_", False),
    # dictation-app leftovers: other alphabets, case-only changes, another machine's paths
    ("donćt", "don't", False),
    ("audit", "AUDIT", False),
    ("my notes", "/Users/archuser/Documents/NOTES", False),
    ("my disk", "/Volumes/omarchyuser/projects/", False),
    ("", "x", False),
])
def test_which_replacements_are_kept(original, target, kept):
    assert vocabulary._keep_replacement(original, target) is kept


@pytest.mark.parametrize("heard,fixed", [
    ("delete no days idle architect now", "delete nodaysidle-architect now"),  # longest wins
    ("delete no days idle now", "delete nodaysidle now"),
    ("No Days Idle", "nodaysidle"),  # case-insensitive
    ("open ai and open router", "OpenAI and OpenRouter"),
    ("ghosty and clod", "Ghostty and clod"),  # "clod" is not in this dictionary
    ("my ghostyfoo", "my ghostyfoo"),  # whole words only
    ("say slash then yes", "say slash then yes"),  # dropped entries stay dropped
    ("", ""),
])
def test_correct(tmp_path, monkeypatch, heard, fixed):
    _dictionary(tmp_path, monkeypatch, [
        ("no days idle", "nodaysidle"), ("no days idle architect", "nodaysidle-architect"),
        ("open ai", "OpenAI"), ("open router", "OpenRouter"), ("ghosty", "Ghostty"),
        ("slash", "/"), ("yes", "Yes please"),
    ])
    assert vocabulary.correct(heard) == fixed


def test_a_replacement_is_not_replaced_again(tmp_path, monkeypatch):
    _dictionary(tmp_path, monkeypatch, [("foo bar", "bar baz"), ("bar baz", "QUX")])
    assert vocabulary.correct("foo bar") == "bar baz"


def test_backslashes_in_a_replacement_are_literal(tmp_path, monkeypatch):
    _dictionary(tmp_path, monkeypatch, [("my path", r"C:\one\1")])
    assert vocabulary.correct("go to my path") == r"go to C:\one\1"


@pytest.mark.parametrize("setting", ["missing", "empty-env", "bad-json", "not-a-dict", "bad-entries"])
def test_unusable_dictionary_leaves_the_transcript_alone(tmp_path, monkeypatch, setting):
    path = tmp_path / "dictionary.json"
    monkeypatch.setenv("KUREK_VOCAB_FILE", str(path))
    if setting == "empty-env":
        _dictionary(tmp_path, monkeypatch, [("no days idle", "nodaysidle")])
        monkeypatch.setenv("KUREK_VOCAB_FILE", "")
    elif setting == "bad-json":
        path.write_text('{"replacements": [', encoding="utf-8")
    elif setting == "not-a-dict":
        path.write_text("[1, 2, 3]", encoding="utf-8")
    elif setting == "bad-entries":
        path.write_text('{"replacements": [1, "x", null, {"original": 5}]}', encoding="utf-8")
    assert vocabulary.correct("no days idle") == "no days idle"


def test_edits_to_the_dictionary_are_picked_up(tmp_path, monkeypatch):
    path = _dictionary(tmp_path, monkeypatch, [("ghosty", "Ghostty")])
    assert vocabulary.correct("ghosty") == "Ghostty"
    path.write_text(json.dumps({"replacements": [{"original": "ghosty", "with": "GHOSTTY"}]}), encoding="utf-8")
    os.utime(path, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
    assert vocabulary.correct("ghosty") == "GHOSTTY"


def test_the_dictionary_file_is_only_read(tmp_path, monkeypatch):
    path = _dictionary(tmp_path, monkeypatch, [("ghosty", "Ghostty")])
    before = path.read_bytes(), path.stat().st_mtime_ns
    vocabulary.correct("ghosty")
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_spoken_text_is_corrected_before_the_prompt_is_handled(tmp_path, monkeypatch):
    _dictionary(tmp_path, monkeypatch, [("no days idle", "nodaysidle")])
    engine = _make_lite_engine(monkeypatch)
    engine.audio_buffer = [np.zeros(160, dtype=np.float32)]
    engine.stt = SimpleNamespace(transcribe=lambda audio, sample_rate: "open no days idle")
    handled: list[str] = []
    engine.handle_text_query = handled.append
    daemon.KurekEngine._process_recorded_audio(engine)
    assert handled == ["open nodaysidle"]
