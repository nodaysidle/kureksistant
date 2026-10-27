"""The unrestricted shell in actions/run.py must be OFF unless KUREK_REMOTE_ALLOW_EXEC=1."""
from __future__ import annotations

import pytest

import actions.run as run_mod
import kurek_daemon as daemon

LEGACY = ("KUREK_UNRESTRICTED_SHELL", "KUREK_ALLOW_SHELL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("KUREK_REMOTE_ALLOW_EXEC", *LEGACY):
        monkeypatch.delenv(var, raising=False)


def test_unrestricted_shell_off_by_default() -> None:
    assert run_mod.is_unrestricted_shell() is False


@pytest.mark.parametrize("legacy", LEGACY)
def test_legacy_vars_alone_do_not_enable(monkeypatch: pytest.MonkeyPatch, legacy: str) -> None:
    monkeypatch.setenv(legacy, "1")
    assert run_mod.is_unrestricted_shell() is False


def test_default_exec_refuses_arbitrary_command(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("KUREK_UNRESTRICTED_SHELL", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KUREK_CODEBASE_ROOTS", str(tmp_path))
    marker = tmp_path / "pwned"
    out = run_mod.run({"action": "exec", "cmd": f"touch {marker} && echo hi"})
    assert not marker.exists()
    assert "Exit: 0" not in out


def test_default_descriptions_do_not_advertise_any_command() -> None:
    assert "arbitrary" not in run_mod.tool_description().lower()
    assert "Sandboxed" in run_mod.tool_description()
    line = daemon._run_tool_prompt_line()
    assert "any command" not in line.lower() and "UNRESTRICTED" not in line


def test_enabled_with_new_var_warns(monkeypatch, capsys) -> None:
    monkeypatch.setenv("KUREK_REMOTE_ALLOW_EXEC", "1")
    monkeypatch.setattr(run_mod, "_UNRESTRICTED_WARNED", False)
    assert run_mod.is_unrestricted_shell() is True
    assert "WARNING" in capsys.readouterr().out
    assert "WARNING" in run_mod.tool_description()
    assert "UNRESTRICTED" in daemon._run_tool_prompt_line()
