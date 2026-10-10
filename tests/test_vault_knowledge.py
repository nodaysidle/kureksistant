"""Vault knowledge path safety, short-query search, and resolver hygiene."""
from __future__ import annotations

from pathlib import Path

import pytest

from actions.vault_knowledge import vault_knowledge


@pytest.fixture()
def vault_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    vault = tmp_path / "nodaysidle-knowledge"
    concepts = vault / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    note = concepts / "sample-note.md"
    note.write_text(
        "---\ntype: concept\n---\n\n# Sample Note\n\nUnique vault content alpha.\n",
        encoding="utf-8",
    )
    # Ensure resolver picks the temp vault via env (no real home vault required).
    for key in ("KUREK_VAULT_DIR", "NODAYSIDLE_VAULT_DIR", "VAULT_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KUREK_VAULT_DIR", str(vault))
    # Avoid ~/.config/kurek/vault_path leaking into the test if present.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "home").mkdir(exist_ok=True)
    return vault


def test_read_rejects_parent_traversal(vault_tmp: Path) -> None:
    result = vault_knowledge(
        {"action": "read", "note_name": "../../etc/passwd"},
    )
    assert result.startswith("Error:")
    assert ".." in result or "traversal" in result.lower()
    assert "VAULT NOTE" not in result


def test_read_rejects_absolute_path(vault_tmp: Path) -> None:
    result = vault_knowledge(
        {"action": "read", "note_name": "/etc/passwd"},
    )
    assert result.startswith("Error:")
    assert "absolute" in result.lower()
    assert "VAULT NOTE" not in result


def test_read_valid_note(vault_tmp: Path) -> None:
    result = vault_knowledge(
        {"action": "read", "note_name": "sample-note"},
    )
    assert result.startswith("[VAULT NOTE:")
    assert "Sample Note" in result
    assert "Unique vault content alpha" in result


def test_search_short_query_returns_no_results(
    vault_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force Python fallback (skip ripgrep) so empty-token vacuous match is exercised.
    monkeypatch.setattr("actions.vault_knowledge.shutil.which", lambda _name: None)
    result = vault_knowledge({"action": "search", "query": "a to of"})
    assert "No notes found matching" in result
    assert "Found notes" not in result


def test_vault_knowledge_source_has_no_home_arch() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "actions" / "vault_knowledge.py").read_text(encoding="utf-8")
    assert "/home/arch" not in text
