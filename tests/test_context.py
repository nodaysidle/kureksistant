"""build_messages: every memory store, in rank order, through one interface."""
from __future__ import annotations

from pathlib import Path

import pytest

from core import context, rules
from memory import ledger, reference


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    (hermes / "USER.md").write_text("Name: Alan\n§\nLikes tea\n", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("- terminal: ghostty\n", encoding="utf-8")
    (tmp_path / "rules.toml").write_text(
        '[[rule]]\nid = "a"\ntext = "Be brief."\npin = true\n', encoding="utf-8"
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(reference, "resolve_hermes_memory_dirs", lambda: [hermes])
    monkeypatch.setattr(rules, "RULES_PATH", tmp_path / "rules.toml")
    monkeypatch.setattr(context, "PROJECTS_DIR", tmp_path / "none")
    return tmp_path


def test_system_prompt_carries_every_store_in_order(home: Path) -> None:
    ledger.append("fact", "city: Koper", "manage_memory call", key="city")
    system = context.build_messages("PERSONA", "hi", [])[0]["content"]
    tags = ["PERSONA", "[USER RULES]", "[NODAYSIDLE PORTFOLIO", "[USER MEMORY & PREFERENCES]",
            "[USER]\nName: Alan\nLikes tea", "[MUSE_CORE_MEMORY]\n- terminal: ghostty", "[LEARNED MEMORY]"]
    positions = [system.index(t) for t in tags]
    assert positions == sorted(positions)
    assert "projects (git repos in ~/dev/nodaysidle): none found." in system


def test_no_memory_means_no_memory_block(home: Path) -> None:
    (home / "MEMORY.md").unlink()
    (home / "hermes" / "USER.md").unlink()
    assert "[USER MEMORY & PREFERENCES]" not in context.build_messages("P", "hi", [])[0]["content"]


def test_history_before_an_outside_correction_is_dropped_and_pins_follow_the_prompt(home: Path) -> None:
    old = ledger.append("fact", "terminal: velocitty", "manage_memory call", key="terminal")
    ledger.append("fact", "terminal: ghostty", "hand edit", supersedes=old)
    history = [
        {"role": "user", "content": "stale", "ts": "2000-01-01T00:00:00"},
        {"role": "user", "content": "fresh", "ts": "2999-01-01T00:00:00"},
        {"role": "assistant", "content": "ok", "tools": ["web_search"], "ts": "2999-01-01T00:00:01"},
    ]
    msgs = context.build_messages("P", "hi", history)
    assert [m["content"].split("\n")[0] for m in msgs[1:-1]] == ["fresh", "ok"]
    assert context.TOOL_NOTE in msgs[2]["content"]
    assert msgs[-1]["content"].startswith("hi\n\n") and "Be brief." in msgs[-1]["content"]
