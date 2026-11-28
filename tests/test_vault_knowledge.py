"""Vault knowledge path safety, short-query search, and resolver hygiene."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from actions.vault_knowledge import vault_knowledge


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def vault_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temp vault in the nodaysidle-vault layout, with decoys outside the knowledge folders."""
    vault = tmp_path / "nodaysidle-vault"
    _write(vault / "wiki" / "hot.md", "---\ntype: meta\n---\n\n# Recent Context\n\n## Projects\n\n- Cascade V3: `main` at `b9ab16d`\n")
    _write(vault / "wiki" / "index.md", "# Index\n\n- Cascade-V3: the index line\n")
    _write(
        vault / "wiki" / "concepts" / "Cascade-V3.md",
        "---\ntype: concept\n---\n\n# Cascade V3\n\n## Current state (2026-11-12)\n\nUnique vault content alpha.\n",
    )
    _write(vault / "wiki" / "sessions" / "2026-11-28-alignment.md", "# Session\n\nAlignment audit notes zeta.\n")
    (vault / "inbox").mkdir()
    _write(vault / "plans" / "secret-plan.md", "# Plan\n\nDecoy phrase quokka in plans.\n")
    _write(vault / ".raw" / "captured" / "raw-capture.md", "# Raw\n\nDecoy phrase quokka in raw.\n")
    _write(vault / "wiki" / "meta" / "ledger.md", "# Ledger\n\nDecoy phrase quokka in meta.\n")
    _write(vault / "graft" / "graft-note.md", "# Graft\n\nDecoy phrase quokka in graft.\n")
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
        {"action": "read", "note_name": "Cascade-V3"},
    )
    assert result.startswith("[VAULT NOTE:")
    assert "Cascade V3" in result
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


def test_add_inbox_never_overwrites_a_capture(vault_tmp: Path) -> None:
    from datetime import datetime

    results = [
        vault_knowledge({"action": "add_inbox", "title": "My Idea", "content": f"take {i}"})
        for i in (1, 2, 3)
    ]
    d = datetime.now().strftime("%Y-%m-%d")
    inbox = vault_tmp / "inbox"
    assert sorted(p.name for p in inbox.iterdir()) == [
        f"{d}-my-idea-2.md", f"{d}-my-idea-3.md", f"{d}-my-idea.md",
    ]
    assert "take 1" in (inbox / f"{d}-my-idea.md").read_text(encoding="utf-8")
    assert results[1] == f"Successfully saved to vault inbox: inbox/{d}-my-idea-2.md"


def test_search_query_starting_with_dashes_is_text(vault_tmp: Path) -> None:
    if not shutil.which("rg"):
        pytest.skip("rg not installed")
    _write(vault_tmp / "wiki" / "concepts" / "dashes.md", "# Dashes\n\nThe marker --not-a-flag appears here.\n")
    result = vault_knowledge({"action": "search", "query": "--not-a-flag"})
    # "occurrences" is the ripgrep path's answer; the Python fallback says "Found notes".
    assert "Found 1 occurrences" in result
    assert "dashes.md" in result and "--not-a-flag appears here" in result
    assert "unexpected argument" not in result and "error" not in result.lower()


# --- vault resolution ---------------------------------------------------------

def test_resolve_order_env_then_config_then_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from actions.vault_knowledge import _resolve_vault_dir

    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    for key in ("KUREK_VAULT_DIR", "NODAYSIDLE_VAULT_DIR", "VAULT_DIR"):
        monkeypatch.delenv(key, raising=False)
    assert _resolve_vault_dir() is None  # the old nodaysidle-knowledge folder is no fallback
    (home / "dev" / "nodaysidle" / "nodaysidle-knowledge").mkdir(parents=True)
    assert _resolve_vault_dir() is None
    default = home / "dev" / "nodaysidle" / "nodaysidle-vault"
    default.mkdir(parents=True)
    assert _resolve_vault_dir() == default.resolve()
    cfg_vault = tmp_path / "cfg-vault"
    cfg_vault.mkdir()
    _write(home / ".config" / "kurek" / "vault_path", f"{cfg_vault}\n")
    assert _resolve_vault_dir() == cfg_vault.resolve()
    env_vault = tmp_path / "env-vault"
    env_vault.mkdir()
    monkeypatch.setenv("VAULT_DIR", str(env_vault))
    assert _resolve_vault_dir() == env_vault.resolve()


# --- list_projects ------------------------------------------------------------

def test_list_projects_reads_hot_md_without_frontmatter(vault_tmp: Path) -> None:
    result = vault_knowledge({"action": "list_projects"})
    assert result.startswith("[NODAYSIDLE PORTFOLIO OVERVIEW from nodaysidle-vault]")
    assert "b9ab16d" in result and "type: meta" not in result


def test_list_projects_falls_back_to_index_then_old_moc(vault_tmp: Path) -> None:
    (vault_tmp / "wiki" / "hot.md").unlink()
    assert "the index line" in vault_knowledge({"action": "list_projects"})
    (vault_tmp / "wiki" / "index.md").unlink()
    _write(vault_tmp / "wiki" / "MOC" / "moc-nodaysidle-knowledge.md", "# Old MOC\n\nold layout overview\n")
    assert "old layout overview" in vault_knowledge({"action": "list_projects"})
    (vault_tmp / "wiki" / "MOC" / "moc-nodaysidle-knowledge.md").unlink()
    result = vault_knowledge({"action": "list_projects"})
    assert "wiki/hot.md" in result and "wiki/index.md" in result and "exists" in result


# --- read ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["Cascade-V3", "cascade-v3", "cascade v3", "Cascade_V3", "CASCADE V3.md", "wiki/concepts/Cascade-V3", "cascade"],
)
def test_read_matches_name_loosely_and_by_path(vault_tmp: Path, name: str) -> None:
    result = vault_knowledge({"action": "read", "note_name": name})
    assert result.startswith("[VAULT NOTE: wiki/concepts/Cascade-V3.md]")
    assert "type: concept" not in result


def test_read_exact_name_beats_contains_match(vault_tmp: Path) -> None:
    _write(vault_tmp / "wiki" / "concepts" / "Cascade-V3-Extra.md", "# Extra\n")
    result = vault_knowledge({"action": "read", "note_name": "cascade v3"})
    assert result.startswith("[VAULT NOTE: wiki/concepts/Cascade-V3.md]")


def test_read_session_page_and_missing_note_suggests(vault_tmp: Path) -> None:
    assert "zeta" in vault_knowledge({"action": "read", "note_name": "alignment"})
    result = vault_knowledge({"action": "read", "note_name": "cascade v9"})
    assert "not found" in result and "Cascade-V3" in result


@pytest.mark.parametrize(
    "name",
    ["secret-plan", "plans/secret-plan", "plans/secret-plan.md", "raw-capture", ".raw/captured/raw-capture",
     "ledger", "wiki/meta/ledger", "graft-note", "AGENTS"],
)
def test_read_never_returns_decoys(vault_tmp: Path, name: str) -> None:
    result = vault_knowledge({"action": "read", "note_name": name})
    assert "VAULT NOTE" not in result and "quokka" not in result


def test_read_refuses_symlink_into_plans(vault_tmp: Path) -> None:
    (vault_tmp / "wiki" / "concepts" / "Sneaky.md").symlink_to(vault_tmp / "plans" / "secret-plan.md")
    result = vault_knowledge({"action": "read", "note_name": "sneaky"})
    assert "VAULT NOTE" not in result and "quokka" not in result


def test_read_caps_long_notes(vault_tmp: Path) -> None:
    _write(vault_tmp / "wiki" / "concepts" / "Long.md", "x" * 9000)
    assert "Truncated" in vault_knowledge({"action": "read", "note_name": "long"})


# --- search -------------------------------------------------------------------

@pytest.mark.parametrize("use_rg", [True, False])
def test_search_finds_knowledge_never_decoys(
    vault_tmp: Path, monkeypatch: pytest.MonkeyPatch, use_rg: bool
) -> None:
    if use_rg and not shutil.which("rg"):
        pytest.skip("rg not installed")
    if not use_rg:
        monkeypatch.setattr("actions.vault_knowledge.shutil.which", lambda _name: None)
    assert "Cascade-V3.md" in vault_knowledge({"action": "search", "query": "alpha"})
    assert "2026-11-28-alignment.md" in vault_knowledge({"action": "search", "query": "zeta"})
    result = vault_knowledge({"action": "search", "query": "quokka"})
    assert result.startswith("No notes found")  # the decoys live in plans/, .raw/, wiki/meta/, graft/


def test_search_skips_missing_folders(vault_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (vault_tmp / "inbox").rmdir()
    assert "Cascade-V3.md" in vault_knowledge({"action": "search", "query": "alpha"})
    monkeypatch.setattr("actions.vault_knowledge.shutil.which", lambda _name: None)
    assert "Cascade-V3.md" in vault_knowledge({"action": "search", "query": "alpha"})


def test_search_finds_inbox_capture(vault_tmp: Path) -> None:
    vault_knowledge({"action": "add_inbox", "title": "Idea", "content": "pangolin thoughts"})
    assert "inbox/" in vault_knowledge({"action": "search", "query": "pangolin"})
