"""Tests for Finding 1: computer_control user_data profile from reference + ledger."""
from __future__ import annotations

from pathlib import Path

import pytest

from actions import computer_control as cc
from memory import ledger, reference


@pytest.fixture
def isolated_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(reference, "resolve_hermes_memory_dirs", lambda: [])
    return tmp_path


def test_user_profile_reads_reference_and_ledger_with_override(isolated_profile: Path) -> None:
    (isolated_profile / "MEMORY.md").write_text(
        "\n".join(
            [
                "# Profile",
                "- Name: Alan Reference",
                "- Email: ref@example.com",
                "- City: Ljubljana",
                "- Not a key value line",
            ]
        ),
        encoding="utf-8",
    )

    ledger.append("fact", "email: ledger@example.com", "user", key="email")
    ledger.append("fact", "- Phone: +15550102030", "manage_memory")
    temp_id = ledger.append("fact", "city: WrongCity", "manage_memory", key="city")
    ledger.retract(temp_id, "city: should be ignored on retract", "user")

    profile = cc._user_profile()
    assert profile["name"] == "Alan Reference"
    assert profile["email"] == "ledger@example.com"
    assert profile["phone"] == "+15550102030"
    assert profile.get("city") != "should be ignored on retract"


def test_user_data_resolves_and_falls_back_to_random(isolated_profile: Path) -> None:
    (isolated_profile / "MEMORY.md").write_text("- Email: boris@example.com\n", encoding="utf-8")
    ledger.append("fact", "name: Boris Test", "user", key="name")

    assert cc.computer_control({"action": "user_data", "field": "Email"}) == "boris@example.com"
    assert cc.computer_control({"action": "user_data", "field": "name"}) == "Boris Test"

    zip_val = cc.computer_control({"action": "user_data", "field": "zip_code"})
    assert len(zip_val) == 5 and zip_val.isdigit()


def test_user_data_falls_back_when_empty(isolated_profile: Path) -> None:
    email_val = cc.computer_control({"action": "user_data", "field": "email"})
    assert "@" in email_val


def test_long_term_json_dependency_removed() -> None:
    src = Path(cc.__file__).read_text(encoding="utf-8")
    assert "long_term.json" not in src
    assert not hasattr(cc, "_MEMORY_PATH")
