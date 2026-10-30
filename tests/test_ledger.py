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
    append("fact", "Sam lives in Oslo", "daily/x.md", db_path=db)
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE ledger SET text = 'Bergen'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM ledger")


@pytest.mark.parametrize("text,source", [("fact", ""), ("fact", "   "), ("", "daily/x.md")])
def test_text_and_source_are_required(db: Path, text: str, source: str) -> None:
    with pytest.raises(LedgerError):
        append("fact", text, source, db_path=db)
    assert ledger.active(db) == []


def test_superseded_rows_leave_the_prompt_but_stay_in_the_table(db: Path) -> None:
    smyth = append("preference", 'address him as "Mr Smyth"', "said 2026-10-15", db_path=db)
    smith = append("preference", 'address him as "Mr Smith"', "said 2026-10-15", supersedes=smyth, db_path=db)
    assert [r["id"] for r in ledger.active(db)] == [smith]
    block = ledger.prompt_block(10_000, db)
    assert "Mr Smith" in block and "Mr Smyth" not in block
    assert sqlite3.connect(db).execute("SELECT count(*) FROM ledger").fetchone()[0] == 2


def test_same_key_supersedes_the_active_row(db: Path) -> None:
    first = append("fact", "city: Bergen", "s", key="Home City", db_path=db)
    second = append("fact", "city: Oslo", "s", key=" home  city ", db_path=db)
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


def test_prompt_block_empty(db: Path) -> None:
    assert ledger.prompt_block(1000, db) == ""


def test_only_corrections_made_outside_the_chat_count(db: Path) -> None:
    assert ledger.outside_correction_ts(db) == ""
    append("fact", "terminal: foot", "manage_memory call 2026-10-01", key="terminal", db_path=db)
    append("fact", "terminal: kitty", "manage_memory call 2026-10-02", key="terminal", db_path=db)
    assert ledger.outside_correction_ts(db) == ""  # a correction made in the chat agrees with the chat
    append("fact", "terminal: ghostty", "owner's correction by hand", key="terminal", db_path=db)
    assert ledger.outside_correction_ts(db) == ledger.active(db)[-1]["ts"]


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
    out = memory_tool.manage_memory({"action": "remember", "key": "city", "value": "Oslo"})
    assert "saved as memory #1" in out
    assert [r["text"] for r in ledger.active()] == ["city: Oslo"]
    assert (tool / "MEMORY.md").read_text(encoding="utf-8") == "# Memory\n"


def test_remember_replaces_by_id_and_recall_sees_only_the_new_one(tool: Path) -> None:
    memory_tool.manage_memory({"action": "remember", "key": "address", "value": "Mr Smyth"})
    memory_tool.manage_memory({"action": "remember", "key": "address_final", "value": "Mr Smith", "replaces": 1})
    found = memory_tool.manage_memory({"action": "recall", "query": "Mr"})
    assert "Mr Smith" in found and "Learned #2" in found
    assert "Learned #1" not in found


def test_recall_finds_past_conversations_by_any_keyword_with_their_date(tool: Path) -> None:
    logs = tool / "memory"
    logs.mkdir()
    (logs / "2026-10-28.md").write_text(
        "\n### [10:00:00] turn\n**User:** the phone app loses the chat when I rotate it\n**Kurek:** Noted.\n"
        "\n### [10:05:00] turn\n**User:** what's the weather\n**Kurek:** Sunny.\n",
        encoding="utf-8",
    )
    found = memory_tool.manage_memory({"action": "recall", "query": "android apk rotate crash"})
    assert "[Said on 2026-10-28]" in found and "rotate" in found
    assert "weather" not in found


def test_remember_with_unknown_replaces_saves_nothing(tool: Path) -> None:
    out = memory_tool.manage_memory({"action": "remember", "key": "k", "value": "v", "replaces": 42})
    assert out.startswith("Nothing was saved")
    assert ledger.active() == []
