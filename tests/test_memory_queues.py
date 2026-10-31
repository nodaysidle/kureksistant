"""Proposals arrive as facts or rules, and never repeat what Kurek already knows."""
from __future__ import annotations

from pathlib import Path

import pytest

import core.dream_cycle as dream_cycle
import core.proposals as proposals
from memory import ledger
from tests.test_core_dream_cycle import _fake_llm, home  # noqa: F401  (fixture)


def test_propose_writes_the_type(tmp_path: Path) -> None:
    p = tmp_path / "p.md"
    assert proposals.propose("dream", "Terminal is Ghostty", source="s", path=p, kind="fact")
    assert proposals.propose("dream", "never push to main", source="s", path=p)
    text = p.read_text()
    assert "- **Type:** fact\n- **Proposed fact:** Terminal is Ghostty" in text
    assert "- **Type:** rule\n- **Proposed rule:** never push to main" in text


def test_same_text_is_a_duplicate_across_kinds(tmp_path: Path) -> None:
    p = tmp_path / "p.md"
    assert proposals.propose("a", "uses Ghostty", source="s", path=p, kind="fact")
    assert not proposals.propose("b", "uses Ghostty", source="s", path=p, kind="rule")


def test_unknown_kind_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        proposals.propose("a", "x", source="s", path=tmp_path / "p.md", kind="memory")


def test_items_follow_their_section() -> None:
    text = ("## Facts about the user\n- lives in Koper\n- None\n"
            "## Rules for how Kurek should behave\n- never push to main\n")
    assert dream_cycle.guidance_items(text) == [("fact", "lives in Koper"), ("rule", "never push to main")]


def test_dream_sees_todays_memory_and_splits_kinds(monkeypatch, home: Path) -> None:  # noqa: F811
    ledger.append("fact", "terminal_emulator: Master Alan's terminal emulator is Ghostty.",
                  "test", key="terminal_emulator")
    prompts = _fake_llm(monkeypatch, [
        "Dream.",
        "## Facts about the user\n- has a 4K monitor\n"
        "## Rules for how Kurek should behave\n- read numbers digit by digit\n",
    ])
    res = dream_cycle.execute_dream_cycle()
    assert "Ghostty" in prompts[1], "the proposal step must see current learned memory"
    assert "it wins over that day's conversation" in prompts[1]
    text = (home / "kurek_proposed_rules.md").read_text()
    assert res["proposed"] == 2
    assert "- **Proposed fact:** has a 4K monitor" in text
    assert "- **Proposed rule:** read numbers digit by digit" in text


def test_dream_drops_what_memory_or_rules_already_say(monkeypatch, home: Path) -> None:  # noqa: F811
    (home / "rules.toml").write_text('[[rule]]\nid = "a"\ntext = "never push to main"\n')
    ledger.append("fact", "terminal_emulator: Master Alan's terminal emulator is Ghostty.", "test")
    _fake_llm(monkeypatch, [
        "Dream.",
        "## Facts about the user\n- Master Alan's terminal emulator is Ghostty\n"
        "## Rules for how Kurek should behave\n- Never push to main.\n- keep a changelog\n",
    ])
    res = dream_cycle.execute_dream_cycle()
    text = (home / "kurek_proposed_rules.md").read_text()
    assert res["proposed"] == 1
    assert "keep a changelog" in text and "Ghostty" not in text
