import sqlite3
from pathlib import Path

import pytest

from actions import memory_tool
from memory import ledger, memory_manager
from memory.ledger import LedgerError, append


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "ledger.db"


def test_rows_cannot_be_updated_or_deleted(db: Path) -> None:
    append("fact", "Alan lives in Koper", "daily/x.md", db_path=db)
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE ledger SET text = 'Ljubljana'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM ledger")


@pytest.mark.parametrize("text,source", [("fact", ""), ("fact", "   "), ("", "daily/x.md")])
def test_text_and_source_are_required(db: Path, text: str, source: str) -> None:
    with pytest.raises(LedgerError):
        append("fact", text, source, db_path=db)
    assert ledger.active(db) == []


def test_superseded_rows_leave_the_prompt_but_stay_in_the_table(db: Path) -> None:
    allen = append("preference", 'address him as "Master Allen"', "said 2026-10-15", db_path=db)
    alan = append("preference", 'address him as "Master Alan"', "said 2026-10-15", supersedes=allen, db_path=db)
    assert [r["id"] for r in ledger.active(db)] == [alan]
    block = ledger.prompt_block(10_000, db)
    assert "Master Alan" in block and "Master Allen" not in block
    assert sqlite3.connect(db).execute("SELECT count(*) FROM ledger").fetchone()[0] == 2


def test_same_key_supersedes_the_active_row(db: Path) -> None:
    first = append("fact", "city: Ljubljana", "s", key="Home City", db_path=db)
    second = append("fact", "city: Koper", "s", key=" home  city ", db_path=db)
    rows = ledger.active(db)
    assert [r["id"] for r in rows] == [second]
    assert sqlite3.connect(db).execute("SELECT supersedes FROM ledger WHERE id=?", (second,)).fetchone()[0] == first


def test_resaving_active_text_is_idempotent(db: Path) -> None:
    assert append("fact", "likes tea", "s1", db_path=db) == append("fact", "likes  tea", "s2", db_path=db)
    assert len(ledger.active(db)) == 1


def test_replacing_a_missing_row_is_refused(db: Path) -> None:
    with pytest.raises(LedgerError, match="#99"):
        append("fact", "x", "s", supersedes=99, db_path=db)
    assert ledger.active(db) == []


def test_prompt_block_empty_and_search(db: Path) -> None:
    assert ledger.prompt_block(1000, db) == ""
    append("fact", "Alan lives in Koper", "s", db_path=db)
    assert [r["text"] for r in ledger.search("KOPER", db)] == ["Alan lives in Koper"]
    assert ledger.search("zagreb", db) == []


def test_prompt_block_over_limit_keeps_newest_and_warns_once(db: Path, capsys) -> None:
    ledger._warned_cut.clear()
    for i in range(6):
        append("fact", f"entry number {i} " + "x" * 40, "s", db_path=db)
    limit = len(ledger.PROMPT_HEADER) + 3 * 80
    block = ledger.prompt_block(limit, db)
    ledger.prompt_block(limit, db)
    assert "entry number 5" in block and "entry number 0" not in block
    assert capsys.readouterr().out.count("WARNING") == 1


@pytest.fixture
def tool(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(memory_manager, "DB_PATH", tmp_path / "kurek_memory.db")
    monkeypatch.setattr(memory_manager, "MEMORY_PATH", tmp_path / "long_term.json")
    monkeypatch.setattr(memory_tool.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(memory_tool, "resolve_hermes_memory_dirs", lambda: [])
    monkeypatch.setattr(memory_tool, "_triage_with_jev", lambda k, v: None)
    (tmp_path / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    return tmp_path


def test_remember_writes_the_ledger_and_leaves_memory_md_alone(tool: Path) -> None:
    out = memory_tool.manage_memory({"action": "remember", "key": "city", "value": "Koper"})
    assert "saved as memory #1" in out
    assert [r["text"] for r in ledger.active()] == ["city: Koper"]
    assert (tool / "MEMORY.md").read_text(encoding="utf-8") == "# Memory\n"


def test_remember_replaces_by_id_and_recall_sees_only_the_new_one(tool: Path) -> None:
    memory_tool.manage_memory({"action": "remember", "key": "address", "value": "Master Allen"})
    memory_tool.manage_memory({"action": "remember", "key": "address_final", "value": "Master Alan", "replaces": 1})
    found = memory_tool.manage_memory({"action": "recall", "query": "Master"})
    assert "Master Alan" in found and "Learned #2" in found
    assert "Learned #1" not in found


def test_remember_with_unknown_replaces_saves_nothing(tool: Path) -> None:
    out = memory_tool.manage_memory({"action": "remember", "key": "k", "value": "v", "replaces": 42})
    assert out.startswith("Nothing was saved")
    assert ledger.active() == []
