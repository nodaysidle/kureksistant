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


def test_descriptions_carry_self_audit_habits(monkeypatch) -> None:
    for desc in (run_mod._SANDBOX_DESCRIPTION, run_mod._UNRESTRICTED_DESCRIPTION):
        assert "Set cwd instead of starting with cd" in desc
        assert "A refusal is final" in desc
        assert "~/dev/nodaysidle" in desc


def test_unrestricted_missing_cwd_refused_not_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("KUREK_REMOTE_ALLOW_EXEC", "1")
    monkeypatch.setattr(run_mod, "_UNRESTRICTED_WARNED", True)
    out = run_mod.run({"action": "exec", "cmd": "pwd", "cwd": str(tmp_path / "nope")})
    assert out.startswith("Refused: cwd")


@pytest.mark.parametrize(
    "cmd,flagged",
    [
        ("ls ~/Projects/forge", True),
        ("ls $HOME/Projects", True),
        ("ls ~/dev/nodaysidle/forge", False),
        ("ls ~/ProjectsX", False),
    ],
)
def test_legacy_projects_note(monkeypatch, cmd, flagged) -> None:
    monkeypatch.setattr(run_mod, "_action_exec", lambda *a: "Exit: 0")
    out = run_mod.run({"action": "exec", "cmd": cmd})
    assert ("retired Mac copy" in out) is flagged


def test_unrestricted_shell_runs_in_own_scope_under_systemd(monkeypatch, tmp_path) -> None:
    import actions.dev_agent as dev_agent

    monkeypatch.setenv("KUREK_REMOTE_ALLOW_EXEC", "1")
    monkeypatch.setattr(run_mod, "_UNRESTRICTED_WARNED", True)
    monkeypatch.setenv("INVOCATION_ID", "test")
    monkeypatch.setattr(dev_agent.shutil, "which", lambda name: f"/usr/bin/{name}")
    seen = {}

    class _Stop(Exception):
        pass

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        raise _Stop

    monkeypatch.setattr(run_mod.subprocess, "Popen", fake_popen)
    run_mod.run({"action": "exec", "cmd": "true", "cwd": str(tmp_path)})
    assert seen["argv"][:4] == ["systemd-run", "--user", "--scope", "--quiet"]
