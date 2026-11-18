"""Tests for Finding 6: actions/clipboard_tool.py uses XDG paths, not repo memory/."""
from __future__ import annotations

from pathlib import Path

import pytest

import actions.clipboard_tool as ct


def test_clipboard_store_uses_xdg_data_home_and_not_repo_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_memory_clip = (
        Path(__file__).resolve().parents[1] / "memory" / "clipboard_history.json"
    )
    existed_before = repo_memory_clip.exists()
    before_bytes = repo_memory_clip.read_bytes() if existed_before else None

    assert ct.CLIPBOARD_FILE != repo_memory_clip
    assert ct.CLIPBOARD_FILE.parent.name == "kurek"
    assert ct.CLIPBOARD_FILE.name == "clipboard_history.json"

    xdg_data = tmp_path / "custom_xdg_data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))

    store = ct.ClipboardStore()
    assert store.filepath == xdg_data / "kurek" / "clipboard_history.json"
    store.add_clip("isolated xdg clip", pinned=True, title="test")
    assert store.filepath.exists()

    # Dynamic override after import also works when filepath is None
    xdg_data_2 = tmp_path / "custom_xdg_data_2"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data_2))
    store2 = ct.ClipboardStore()
    assert store2.filepath == xdg_data_2 / "kurek" / "clipboard_history.json"
    store2.add_clip("second clip")
    assert store2.filepath.exists()

    if existed_before:
        assert repo_memory_clip.read_bytes() == before_bytes
    else:
        assert not repo_memory_clip.exists()


def test_clipboard_store_uses_home_fallback_when_xdg_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    store = ct.ClipboardStore()
    assert (
        store.filepath
        == tmp_path / ".local" / "share" / "kurek" / "clipboard_history.json"
    )
    store.add_clip("home fallback clip")
    assert store.filepath.exists()
