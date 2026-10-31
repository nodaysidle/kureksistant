"""Dream cycle: reflects on yesterday, no stub on LLM failure, proposes rules without touching the active ones."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

import core.dream_cycle as dream_cycle
import core.proposals as proposals


@pytest.fixture
def home(monkeypatch, tmp_path: Path) -> Path:
    for sub in ("dreams", "memory"):
        (tmp_path / sub).mkdir()
    monkeypatch.setattr(dream_cycle, "HOME", tmp_path)
    monkeypatch.setattr(dream_cycle, "DREAMS_DIR", tmp_path / "dreams")
    monkeypatch.setattr(dream_cycle, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(proposals, "PROPOSALS_PATH", tmp_path / "kurek_proposed_rules.md")
    monkeypatch.setattr("core.rules.RULES_PATH", tmp_path / "rules.toml")
    monkeypatch.setattr(dream_cycle, "STATE_FILE", tmp_path / "ALIGNMENT_STATE.yaml")
    monkeypatch.setattr(dream_cycle, "REPAIRS_FILE", tmp_path / "REPAIR_THREADS.yaml")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("memory.reference.resolve_hermes_memory_dirs", lambda: [])
    return tmp_path


def _fake_llm(monkeypatch, responses):
    it = iter(responses)
    prompts = []

    def fake(**kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        return next(it)

    monkeypatch.setattr("core.llm_client.query_deepseek", fake)
    return prompts


def _yesterday() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def test_defaults_to_yesterday(monkeypatch, home: Path) -> None:
    y = _yesterday()
    today = datetime.now().strftime("%Y-%m-%d")
    (home / "memory" / f"{y}.md").write_text("YESTERDAY-MARKER work\n", encoding="utf-8")
    (home / "memory" / f"{today}.md").write_text("TODAY-MARKER work\n", encoding="utf-8")
    prompts = _fake_llm(monkeypatch, ["Dream prose.", "- guidance"])

    res = dream_cycle.execute_dream_cycle()
    assert res["status"] == "success"
    assert res["date"] == y
    assert res["dream_file"] == str(home / "dreams" / f"{y}.md")
    assert (home / "dreams" / f"{y}.md").exists()
    assert not (home / "dreams" / f"{today}.md").exists()
    assert "YESTERDAY-MARKER" in prompts[0] and "TODAY-MARKER" not in prompts[0]

    skipped = dream_cycle.execute_dream_cycle()
    assert skipped["status"] == "skipped"


def test_explicit_day(monkeypatch, home: Path) -> None:
    _fake_llm(monkeypatch, ["Dream.", None])
    res = dream_cycle.execute_dream_cycle(day="2026-01-02")
    assert res["date"] == "2026-01-02"
    assert (home / "dreams" / "2026-01-02.md").exists()


@pytest.mark.parametrize("bad", [None, "", {"content": None}])
def test_llm_failure_writes_nothing(monkeypatch, home: Path, bad) -> None:
    _fake_llm(monkeypatch, [bad])
    res = dream_cycle.execute_dream_cycle()
    assert res["status"] == "error"
    assert res["message"]
    assert list((home / "dreams").iterdir()) == []
    assert not (home / "ALIGNMENT_SYNTHESIS.md").exists()


def test_never_touches_alignment_or_rules(monkeypatch, home: Path) -> None:
    align = home / "ALIGNMENT_SYNTHESIS.md"
    rules = home / "rules.toml"
    align.write_text("- **[BOUNDARY]** never push to main\n", encoding="utf-8")
    rules.write_text('[[rule]]\nid = "a"\ntext = "keep"\n', encoding="utf-8")
    before = (align.read_bytes(), rules.read_bytes())
    _fake_llm(monkeypatch, ["Dream.", "## Active\n- new guidance\n## Non-Negotiable Boundaries\n- never rm"])

    res = dream_cycle.execute_dream_cycle()

    assert res["status"] == "success" and res["proposed"] == 2
    assert (align.read_bytes(), rules.read_bytes()) == before
    text = (home / "kurek_proposed_rules.md").read_text(encoding="utf-8")
    assert "- **Proposed rule:** new guidance" in text
    assert "- **Proposed rule:** never rm" in text


def test_rerun_does_not_duplicate_proposals(monkeypatch, home: Path) -> None:
    _fake_llm(monkeypatch, ["Dream.", "- same guidance"])
    dream_cycle.execute_dream_cycle()
    _fake_llm(monkeypatch, ["Dream 2.", "- same guidance"])
    assert dream_cycle.execute_dream_cycle(force=True)["proposed"] == 0
    assert (home / "kurek_proposed_rules.md").read_text().count("same guidance") == 1



def test_prompt_shows_existing_rules_and_allows_none(monkeypatch, home: Path) -> None:
    (home / "rules.toml").write_text('[[rule]]\nid = "a"\ntext = "never push to main"\n', encoding="utf-8")
    prompts = _fake_llm(monkeypatch, ["Dream.", "NONE"])

    res = dream_cycle.execute_dream_cycle()

    assert res["proposed"] == 0
    assert "- [a] never push to main" in prompts[1]
    assert "return exactly: NONE" in prompts[1]
    assert not (home / "kurek_proposed_rules.md").exists()


def test_none_bullets_are_not_proposals() -> None:
    text = ("## Boundaries\n- NONE\n- (none)\n- None.\n- None new beyond existing rules.\n"
            "- No explicit negative boundaries were stated.\n- Nothing new.\n- keep replies short\n- Noted names are spelled out")
    assert dream_cycle.guidance_bullets(text) == ["keep replies short", "Noted names are spelled out"]
