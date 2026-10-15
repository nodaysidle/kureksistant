"""Tests for actions/run.py — sandboxed execution, allowlisting, refusals, audit logs, and redaction."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import kurek_daemon as daemon
from actions.run import run, _get_audit_log_path, _validate_command, _redact_secrets


@pytest.fixture
def run_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = tmp_path / "dev" / "run_sandbox"
    ws.mkdir(parents=True)

    # Initialize a clean git repo
    subprocess.run(["git", "init", "-b", "main", str(ws)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "Test User"], check=True)

    (ws / "hello.txt").write_text("Safe read file line 1\nLine 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(ws), "add", "."], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-m", "First commit"], check=True)

    monkeypatch.setenv("KUREK_CODEBASE_ROOTS", str(ws))
    monkeypatch.setenv("HOME", str(tmp_path))

    return ws


def test_exec_allowlisted_read_only(run_workspace: Path) -> None:
    # 1. git status
    res_git = run({"action": "exec", "cmd": "git status", "cwd": str(run_workspace)})
    assert "Exit: 0" in res_git
    assert "On branch main" in res_git

    # 2. git log
    res_log = run({"action": "exec", "cmd": "git log -n 1 --oneline", "cwd": str(run_workspace)})
    assert "Exit: 0" in res_log
    assert "First commit" in res_log

    # 3. ls
    res_ls = run({"action": "exec", "cmd": "ls -la", "cwd": str(run_workspace)})
    assert "Exit: 0" in res_ls
    assert "hello.txt" in res_ls


def test_exec_destructive_command_refused(run_workspace: Path) -> None:
    # 1. rm is permanently blocked
    res_rm = run({"action": "exec", "cmd": "rm hello.txt", "cwd": str(run_workspace)})
    assert "Refused:" in res_rm
    assert "blocked" in res_rm.lower() or "disallowed" in res_rm.lower()
    assert (run_workspace / "hello.txt").exists()  # verified file was NOT deleted

    # 2. git push is blocked
    res_push = run({"action": "exec", "cmd": "git push origin main", "cwd": str(run_workspace)})
    assert "Refused:" in res_push
    assert "git push" in res_push

    # 3. git reset --hard is blocked
    res_reset = run({"action": "exec", "cmd": "git reset --hard HEAD~1", "cwd": str(run_workspace)})
    assert "Refused:" in res_reset

    # 4. package manager install is blocked
    res_npm = run({"action": "exec", "cmd": "npm install malicious-pkg", "cwd": str(run_workspace)})
    assert "Refused:" in res_npm


def test_shell_chaining_refused(run_workspace: Path) -> None:
    res_chain = run({"action": "exec", "cmd": "git status && rm hello.txt", "cwd": str(run_workspace)})
    assert "Refused:" in res_chain
    assert "metacharacter" in res_chain.lower() or "chaining" in res_chain.lower()
    assert (run_workspace / "hello.txt").exists()

    res_pipe = run({"action": "exec", "cmd": "ls | grep hello", "cwd": str(run_workspace)})
    assert "Refused:" in res_pipe


def test_cwd_outside_allowed_roots_refused(run_workspace: Path, tmp_path: Path) -> None:
    outside = tmp_path / "etc"
    outside.mkdir(parents=True)
    res = run({"action": "exec", "cmd": "ls", "cwd": str(outside)})
    assert "Refused:" in res
    assert "outside allowed project roots" in res


def test_audit_log_written_and_append_only(run_workspace: Path) -> None:
    audit_file = _get_audit_log_path()
    if audit_file.exists():
        audit_file.unlink()

    # Successful exec
    run({"action": "exec", "cmd": "git status", "cwd": str(run_workspace)})
    assert audit_file.exists()
    content1 = audit_file.read_text(encoding="utf-8")
    assert "STATUS=completed" in content1
    assert "git status" in content1

    # Refused command also logged to audit
    run({"action": "exec", "cmd": "rm -rf /", "cwd": str(run_workspace)})
    content2 = audit_file.read_text(encoding="utf-8")
    assert len(content2) > len(content1)
    assert "STATUS=refused" in content2
    assert "rm -rf" in content2


def test_output_capping_on_long_output(run_workspace: Path) -> None:
    # Create python test script printing 120 lines
    (run_workspace / "print_many.py").write_text(
        'for i in range(120):\n    print(f"item_{i}")\n',
        encoding="utf-8",
    )
    # Using python -m unittest discovery or pytest
    # But python allows -m unittest / -m pytest, or we can use cat on long file
    (run_workspace / "long.txt").write_text("\n".join(f"line_{i}" for i in range(150)), encoding="utf-8")
    res = run({"action": "exec", "cmd": "cat long.txt", "cwd": str(run_workspace)})

    assert "lines truncated" in res
    assert "Total lines: 150" in res
    assert "line_0" in res  # head preserved
    assert "line_149" in res  # tail preserved


def test_credential_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-FAKE_TEST_VALUE_not_a_real_key_00")
    monkeypatch.setenv("XAI_API_KEY", "xai-secrettoken1234567890abcdef")

    raw = (
        "Output containing sk-FAKE_TEST_VALUE_not_a_real_key_00 and "
        "another key sk-999888777666555444333222111 and "
        "xai-secrettoken1234567890abcdef and "
        "Bearer secret_bearer_token_1234567890 and "
        "clone https://user:supersecretpass@github.com/repo.git"
    )

    redacted = _redact_secrets(raw)
    assert "sk-abcdef" not in redacted
    assert "xai-secret" not in redacted
    assert "supersecretpass" not in redacted
    assert "[REDACTED_DEEPSEEK_API_KEY]" in redacted or "[REDACTED_DEEPSEEK_KEY]" in redacted
    assert "[REDACTED_USER]:[REDACTED_PASS]" in redacted


def test_run_list_and_read(run_workspace: Path) -> None:
    exec_res = run({"action": "exec", "cmd": "git status", "cwd": str(run_workspace)})
    assert "Exit: 0" in exec_res

    # List runs
    list_res = run({"action": "list"})
    assert "Recent Runs:" in list_res
    assert "git status" in list_res

    # Read latest run
    read_res = run({"action": "read"})
    assert "Command: git status" in read_res
    assert "Status: completed" in read_res


def test_remote_blocks_run_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KUREK_REMOTE_ALLOW_EXEC", raising=False)
    engine = daemon.KurekEngine.__new__(daemon.KurekEngine)
    engine.actions = SimpleNamespace(run=MagicMock(return_value="executed"))

    blocked = engine._run_tool("run", {"action": "exec", "cmd": "git status"}, remote=True)
    assert "disabled for remote" in blocked
    engine.actions.run.assert_not_called()

    # Allowed when KUREK_REMOTE_ALLOW_EXEC=1
    monkeypatch.setenv("KUREK_REMOTE_ALLOW_EXEC", "1")
    allowed = engine._run_tool("run", {"action": "exec", "cmd": "git status"}, remote=True)
    assert allowed == "executed"
    engine.actions.run.assert_called_once()


def test_no_hardcoded_home_arch() -> None:
    root = Path(__file__).resolve().parents[1]
    source_file = root / "actions" / "run.py"
    text = source_file.read_text(encoding="utf-8")
    assert "/home/arch" not in text
