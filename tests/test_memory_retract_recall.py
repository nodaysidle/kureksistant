"""Retractions keep history but leave the prompt; Jev only proposes; Hermes is
read-only; recall over the daily logs is ranked and safe against FTS syntax."""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from actions import memory_tool
from core import proposals
from memory import ledger, reference

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_jev(monkeypatch):
    monkeypatch.setattr(memory_tool, "_triage_with_jev", lambda k, v: None)


# -- ledger.retract ---------------------------------------------------------

def test_retract_hides_target_and_itself_from_the_prompt():
    keep = ledger.append("fact", "home_city: Koper", "manage_memory call", key="home_city")
    gone = ledger.append("fact", "terminal: Velocity", "manage_memory call", key="terminal")
    rid = ledger.retract(gone, "void entry", "manage_memory call")

    block = ledger.prompt_block(6000)
    assert f"#{keep}]" in block
    assert "Velocity" not in block and "void entry" not in block
    active = {r["id"]: r["kind"] for r in ledger.active()}
    assert active.get(rid) == "retract" and gone not in active


def test_ledger_is_still_append_only():
    mid = ledger.append("fact", "a: b", "manage_memory call")
    ledger.retract(mid, "wrong", "manage_memory call")
    conn = sqlite3.connect(ledger.DB_PATH)
    for sql in ("UPDATE ledger SET text='x'", "DELETE FROM ledger"):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)
    conn.close()


def test_retract_missing_or_already_superseded_raises():
    with pytest.raises(ledger.LedgerError):
        ledger.retract(999, "nope", "manage_memory call")
    old = ledger.append("fact", "terminal: foot", "manage_memory call", key="terminal")
    ledger.append("fact", "terminal: Ghostty", "manage_memory call", key="terminal")
    with pytest.raises(ledger.LedgerError):
        ledger.retract(old, "again", "manage_memory call")


def test_retract_needs_reason_and_source():
    mid = ledger.append("fact", "a: b", "manage_memory call")
    with pytest.raises(ledger.LedgerError):
        ledger.retract(mid, "  ", "manage_memory call")


# -- manage_memory ----------------------------------------------------------

def test_forget_through_the_tool():
    out = memory_tool.manage_memory({"action": "remember", "category": "preferences",
                                     "key": "bikes", "value": "dislikes bicycles"})
    mid = int(re.search(r"#(\d+)", out).group(1))
    assert memory_tool.manage_memory({"action": "forget", "replaces": mid}) == f"Forgot memory #{mid}."
    assert "bicycles" not in ledger.prompt_block(6000)
    recalled = memory_tool.manage_memory({"action": "recall", "query": "forgotten"})
    assert f"[Retracted #{mid}]" in recalled
    assert "forget" in memory_tool.TOOL["parameters"]["properties"]["action"]["enum"]


def test_forget_without_id_saves_nothing():
    assert "#id" in memory_tool.manage_memory({"action": "forget"})
    assert ledger.active() == []


@pytest.mark.parametrize("category,kind", [
    ("preferences", "preference"), ("identity", "fact"), ("projects", "fact"),
    ("relationships", "fact"), ("wishes", "preference"), ("notes", "note"),
])
def test_category_maps_to_one_kind(category, kind):
    memory_tool.manage_memory({"action": "remember", "category": category, "key": "k", "value": category})
    assert ledger.active()[-1]["kind"] == kind


def test_remember_leaves_hermes_untouched(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    (hermes / "MEMORY.md").write_text("original\n")
    monkeypatch.setattr(reference, "resolve_hermes_memory_dirs", lambda: [hermes])
    memory_tool.manage_memory({"action": "remember", "key": "coffee", "value": "black"})
    assert (hermes / "MEMORY.md").read_text() == "original\n"


# -- Jev triage only proposes ----------------------------------------------

def _fake_typesafe(kind: str, salience: float, noul: float):
    answers = {
        "is_durable": SimpleNamespace(noul=noul),
        "kind": SimpleNamespace(choice=kind),
        "salience": SimpleNamespace(score=salience, confidence=0.9),
    }

    class Client:
        def system_one(self, **_kw):
            return SimpleNamespace(answers=answers)

    q = lambda **kw: kw  # noqa: E731
    return SimpleNamespace(TypeSafeClient=Client, Noul=q, Choice=q, Score=q)


def test_jev_triage_proposes_and_never_writes_the_ledger(tmp_path, monkeypatch):
    import kurek_daemon as daemon
    import memory.config_manager as cm

    monkeypatch.setitem(sys.modules, "typesafe_sdk", _fake_typesafe("fact", 2.0, 0.9))
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(cm, "load_api_keys", lambda *a, **k: None)
    props = tmp_path / "proposals.md"
    monkeypatch.setattr(proposals, "PROPOSALS_PATH", props)

    engine = object.__new__(daemon.KurekEngine)
    engine._consolidate_with_jev("I always use Ghostty as my terminal.")
    assert ledger.active() == []
    assert "Ghostty" in props.read_text()


def test_daemon_never_writes_the_ledger():
    assert "ledger.append" not in (ROOT / "kurek_daemon.py").read_text()


# -- recall over the daily logs --------------------------------------------

@pytest.fixture
def logs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "memory").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    log = home / "memory" / "2026-10-01.md"
    log.write_text(
        "\n### [10:00:00] turn\n**User:** the app crashed on rotate\n**Kurek:** noted\n"
        "\n### [11:00:00] turn\n**User:** start the timer\n**Kurek:** started\n"
    )
    return log


def _daily_hits(query: str) -> list[str]:
    return memory_tool._search_daily_logs(memory_tool._keywords(query))


def test_recall_matches_word_forms(logs):
    hits = _daily_hits("crash")
    assert hits and "crashed on rotate" in hits[0] and "[Said on 2026-10-01]" in hits[0]


def test_recall_does_not_match_inside_words(logs):
    assert not any("timer" in h for h in _daily_hits("art"))
    assert memory_tool._score("start the timer", {"art"}) == 0
    assert memory_tool._score("it crashed", {"crash"}) == 1


def test_recall_reindexes_changed_and_deleted_logs(logs):
    assert _daily_hits("crash")
    with logs.open("a") as f:
        f.write("\n### [12:00:00] turn\n**User:** the gradle build broke\n**Kurek:** ok\n")
    os.utime(logs, (time.time() + 5, time.time() + 5))
    assert any("gradle" in h for h in _daily_hits("gradle"))
    logs.unlink()
    assert _daily_hits("crash") == []


def test_recall_query_cannot_inject_fts_syntax(logs):
    for nasty in ['" OR * NEAR(', 'crash"', "NOT AND", "col:crash", "^crash*"]:
        memory_tool._search_daily_logs(memory_tool._keywords(nasty) | {nasty})
