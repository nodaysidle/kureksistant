"""Unit tests for SQLite WAL + FTS5 memory_manager upgrade."""
from __future__ import annotations

from pathlib import Path
import pytest
from memory import memory_manager
from actions import background_monitor


def test_sqlite_memory_remember_and_search(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test_memory.db"
    test_json = tmp_path / "test_memory.json"
    monkeypatch.setattr(memory_manager, "DB_PATH", test_db)
    monkeypatch.setattr(memory_manager, "MEMORY_PATH", test_json)

    res = memory_manager.remember("pet", "fluffy orange cat", category="preferences")
    assert "Remembered: preferences/pet = fluffy orange cat" in res

    mem = memory_manager.load_memory()
    assert mem["preferences"]["pet"]["value"] == "fluffy orange cat"

    # Search with FTS5 / lexical fallback
    search_res = memory_manager.search_memory("orange")
    assert "preferences/pet: fluffy orange cat" in search_res

    # Forget
    forget_res = memory_manager.forget("pet", category="preferences")
    assert "Forgotten: preferences/pet" in forget_res

    search_after = memory_manager.search_memory("orange")
    assert "Nothing stored" in search_after


def test_background_monitor_decoupled_lock(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test_monitor_memory.db"
    test_json = tmp_path / "test_monitor_memory.json"
    monkeypatch.setattr(memory_manager, "DB_PATH", test_db)
    monkeypatch.setattr(memory_manager, "MEMORY_PATH", test_json)

    # Ensure background_monitor does not use private _lock
    assert not hasattr(background_monitor, "_lock")

    background_monitor.add_monitor("linux kernel")
    monitors = memory_manager.load_monitors()
    assert any("linux" in k for k in monitors.keys())


def test_failed_migration_is_retried_and_keeps_legacy_json(tmp_path: Path, monkeypatch):
    import json
    import sqlite3

    test_db = tmp_path / "mig.db"
    test_json = tmp_path / "long_term.json"
    original = {"identity": {"name": {"value": "Adlof", "updated": "2026-10-01"}},
                "sessions": [{"date": "2026-10-01", "summary": "hi"}]}
    test_json.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(memory_manager, "DB_PATH", test_db)
    monkeypatch.setattr(memory_manager, "MEMORY_PATH", test_json)

    real_loads = json.loads

    def boom(*a, **k):
        raise ValueError("simulated migration failure")

    monkeypatch.setattr(memory_manager.json, "loads", boom)
    memory_manager.load_memory()
    # A write while migration is incomplete must not clobber the legacy file.
    monkeypatch.setattr(memory_manager.json, "loads", boom)
    memory_manager.remember("x", "y")
    monkeypatch.setattr(memory_manager.json, "loads", real_loads)

    conn = sqlite3.connect(str(test_db))
    row = conn.execute("SELECT value FROM metadata WHERE key='migrated'").fetchone()
    assert row is None
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    conn.close()
    assert real_loads(test_json.read_text(encoding="utf-8")) == original

    # Next start retries and succeeds.
    mem = memory_manager.load_memory()
    assert mem["identity"]["name"]["value"] == "Adlof"
    assert len(mem.get("sessions", [])) == 1
    conn = sqlite3.connect(str(test_db))
    row = conn.execute("SELECT value FROM metadata WHERE key='migrated'").fetchone()
    assert row[0] == "1"
    conn.close()
