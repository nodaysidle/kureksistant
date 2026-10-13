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
