"""Tests for memory/paths.py (XDG directories and one-shot legacy migration)."""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

from memory import ledger, paths, recall_index


def test_xdg_dirs_respect_env_and_expanduser(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", "~/custom_data")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "custom_state"))

    assert paths.data_dir() == tmp_path / "custom_data" / "kurek"
    assert paths.state_dir() == tmp_path / "custom_state" / "kurek"
    assert paths.db_path() == tmp_path / "custom_data" / "kurek" / "kurek_memory.db"
    assert (
        paths.clipboard_path()
        == tmp_path / "custom_data" / "kurek" / "clipboard_history.json"
    )
    assert (
        paths.history_path()
        == tmp_path / "custom_state" / "kurek" / "kurek_history.json"
    )


def test_xdg_dirs_fallback_when_unset_or_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", "   ")

    assert paths.data_dir() == tmp_path / ".local" / "share" / "kurek"
    assert paths.state_dir() == tmp_path / ".local" / "state" / "kurek"


def test_migrate_legacy_file_skips_when_missing_or_target_exists(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "old.json"
    target = tmp_path / "new" / "target.json"

    assert paths.migrate_legacy_file(legacy, target) is False

    legacy.write_text('{"old": 1}', encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"new": 2}', encoding="utf-8")

    assert paths.migrate_legacy_file(legacy, target) is False
    assert target.read_text(encoding="utf-8") == '{"new": 2}'
    assert legacy.exists()


def test_migrate_legacy_file_moves_json_privately(tmp_path: Path) -> None:
    legacy = tmp_path / "kurek_history.json"
    target = tmp_path / "state" / "kurek" / "kurek_history.json"
    legacy.write_text('[{"role": "user", "content": "hi"}]', encoding="utf-8")

    assert paths.migrate_legacy_file(legacy, target) is True
    assert not legacy.exists()
    assert target.read_text(encoding="utf-8") == '[{"role": "user", "content": "hi"}]'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_migrate_legacy_sqlite_merges_wal_and_cleans_siblings(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.db"
    target = tmp_path / "data" / "kurek" / "kurek_memory.db"

    conn = sqlite3.connect(str(legacy), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO items (val) VALUES ('from-wal')")
    # Keep a second connection open briefly or ensure WAL exists before closing.
    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    assert Path(f"{legacy}-wal").exists() or legacy.exists()
    conn.close()

    assert paths.migrate_legacy_file(legacy, target) is True
    assert not legacy.exists()
    assert not Path(f"{legacy}-wal").exists()
    assert not Path(f"{legacy}-shm").exists()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    check = sqlite3.connect(str(target))
    assert check.execute("SELECT val FROM items").fetchall() == [("from-wal",)]
    check.close()


def test_migrate_legacy_runtime_files_only_moves_known_files(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg_data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))

    repo = tmp_path / "repo"
    mem = repo / "memory"
    mem.mkdir(parents=True)
    (mem / "kurek_history.json").write_text("[]", encoding="utf-8")
    (mem / "clipboard_history.json").write_text("[]", encoding="utf-8")
    ledger.append("fact", "city: Koper", "test", db_path=mem / "kurek_memory.db")

    migrated = paths.migrate_legacy_runtime_files(base_dir=repo)
    assert migrated == {"db": True, "history": True, "clipboard": True}
    assert paths.history_path().read_text(encoding="utf-8") == "[]"
    assert paths.clipboard_path().read_text(encoding="utf-8") == "[]"
    assert [r["text"] for r in ledger.active(paths.db_path())] == ["city: Koper"]
    assert not (mem / "kurek_memory.db").exists()
    assert not (mem / "kurek_history.json").exists()
    assert not (mem / "clipboard_history.json").exists()


def test_ledger_defaults_to_xdg_and_connect_never_auto_migrates(
    tmp_path: Path, monkeypatch
) -> None:
    repo_db = Path(ledger.__file__).resolve().parent / "kurek_memory.db"
    assert ledger._INITIAL_DB_PATH != repo_db
    assert ledger._INITIAL_DB_PATH.name == "kurek_memory.db"
    assert ledger._INITIAL_DB_PATH.parent.name == "kurek"

    # When DB_PATH is not overridden, resolve_db_path follows XDG_DATA_HOME dynamically.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(ledger, "DB_PATH", ledger._INITIAL_DB_PATH)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "dyn_data"))
    expected = tmp_path / "dyn_data" / "kurek" / "kurek_memory.db"
    assert ledger.resolve_db_path(None) == expected

    # And _connect never calls migrate_legacy_file.
    called = False

    def _forbid(*_a, **_kw):
        nonlocal called
        called = True
        raise AssertionError("migration must not run inside _connect")

    monkeypatch.setattr(paths, "migrate_legacy_file", _forbid)
    ledger.append("fact", "isolated: yes", "test")
    assert not called
    assert expected.exists()
    assert recall_index.search({"isolated"}) == []
