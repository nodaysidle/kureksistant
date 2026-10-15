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


# ---------------------------------------------------------------------------
# Regression tests for the ab1420a security review (one per PoC)
# Fake secrets are assembled at runtime so secret scanners don't flag the source.
# ---------------------------------------------------------------------------

import signal

from actions import run as run_mod

FAKE_GOOGLE = "AI" + "za" + "Q" * 35
FAKE_TS = "ts" + "key-auth-" + "k" * 12 + "-" + "Z9" * 10
FAKE_PROJ = "sk-" + "proj-" + "P1" * 16
FAKE_ANT = "sk-" + "ant-" + "A1" * 16
FAKE_PLAIN = "plain" + "FAKEsecretvalue9999"


def _exec(ws: Path, cmd: str, **kw) -> str:
    return run({"action": "exec", "cmd": cmd, "cwd": str(ws), **kw})


@pytest.mark.parametrize("cmd", [
    "rg --pre=rm x victim.txt",
    "rg --pre rm x victim.txt",
    "rg --pre-glob '*' --pre=rm x victim.txt",
    "rg -z x victim.txt",
    "rg -nz x victim.txt",
    "rg --search-zip x victim.txt",
    "rg --hostname-bin=rm x victim.txt",
])
def test_rg_pre_refused(run_workspace: Path, cmd: str) -> None:
    victim = run_workspace / "victim.txt"
    victim.write_text("x\n", encoding="utf-8")
    res = _exec(run_workspace, cmd)
    assert res.startswith("Refused:"), res
    assert victim.exists()


def test_rg_normal_search_still_works(run_workspace: Path) -> None:
    res = _exec(run_workspace, "rg -n Safe hello.txt")
    assert "Exit: 0" in res and "Safe read file" in res


@pytest.mark.parametrize("cmd", [
    "make -fMk test",
    "make -f Mk test",
    "make --file=Mk test",
    "make --makefile=Mk test",
    "make --eval=test: test",
    "make -C sub test",
    "make --directory=sub test",
    "make -I sub test",
    "make -E x test",
    "make CC=touch test",
    "just --justfile Mk test",
    "just --shell sh test",
])
def test_make_and_just_dangerous_flags_refused(run_workspace: Path, cmd: str) -> None:
    marker = run_workspace / "make_pwned"
    (run_workspace / "Mk").write_text(f"test:\n\ttouch {marker}\n", encoding="utf-8")
    (run_workspace / "sub").mkdir(exist_ok=True)
    res = _exec(run_workspace, cmd)
    assert res.startswith("Refused:"), res
    assert not marker.exists()


@pytest.mark.parametrize("cmd", ["make test", "make -n test", "make -j4 -k test", "make -s build"])
def test_make_safe_forms_validate(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert ok, reason


@pytest.mark.parametrize("cmd", [
    "cargo build --config 'build.rustc-wrapper=\"/tmp/wrap.sh\"'",
    "cargo build --config=build.rustc-wrapper=x",
    "cargo test --target-dir /tmp/elsewhere",
    "cargo check --target-dir=/tmp/elsewhere",
    "cargo build -Zunstable-options",
    "cargo build -Z unstable-options",
    "cargo test --manifest-path /tmp/x/Cargo.toml",
    "cargo +nightly build",
])
def test_cargo_config_and_target_dir_refused(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert not ok and reason.startswith("Refused:"), cmd


@pytest.mark.parametrize("cmd", ["cargo test", "cargo build --release", "cargo test -- --nocapture --config x"])
def test_cargo_safe_forms_validate(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert ok, reason


@pytest.mark.parametrize("argv0", ["x/cat", "./x/cat", "/usr/bin/cat", "~/cat", "../cat"])
def test_path_qualified_argv0_refused(run_workspace: Path, argv0: str) -> None:
    marker = run_workspace / "arbitrary_ran"
    (run_workspace / "x").mkdir(exist_ok=True)
    script = run_workspace / "x" / "cat"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    script.chmod(0o755)
    res = _exec(run_workspace, f"{argv0} hello.txt")
    assert res.startswith("Refused:"), res
    assert "Path-qualified" in res
    assert not marker.exists()


def test_bare_binary_runs_from_path(run_workspace: Path) -> None:
    res = _exec(run_workspace, "cat hello.txt")
    assert "Exit: 0" in res and "Safe read file line 1" in res


@pytest.mark.parametrize("cmd", [
    "git diff --output out.txt HEAD",
    "git diff --output=out.txt HEAD",
    "git log -o out.txt",
    "git show --output out.txt HEAD",
    "git diff --no-index hello.txt hello.txt",
    "git blame --contents hello.txt hello.txt",
    "git diff --ext-diff",
])
def test_git_output_and_out_of_repo_reads_refused(run_workspace: Path, cmd: str) -> None:
    res = _exec(run_workspace, cmd)
    assert res.startswith("Refused:"), res
    assert not (run_workspace / "out.txt").exists()


def _git_refs(ws: Path) -> tuple[str, str, str]:
    def g(*a: str) -> str:
        return subprocess.run(["git", "-C", str(ws), *a], capture_output=True, text=True).stdout
    return g("branch", "--list"), g("tag", "--list"), g("stash", "list")


@pytest.mark.parametrize("cmd", [
    "git branch newbranch",
    "git branch -D main",
    "git branch -d main",
    "git branch --delete main",
    "git branch -m renamed",
    "git branch -M main renamed",
    "git branch -c main copied",
    "git branch -f main HEAD",
    "git branch -a newbranch",
    "git branch -u origin/main",
    "git branch -vD main",
    "git tag v9.9.9",
    "git tag -d v1",
    "git tag --delete v1",
    "git tag -a v2 -m msg",
    "git tag -f v1",
    "git tag -s v3",
    "git stash",
    "git stash push",
    "git stash pop",
    "git stash drop",
    "git stash clear",
    "git config --add user.name x --get",
    "git config --unset user.name --get",
])
def test_git_ref_and_stash_mutations_refused(run_workspace: Path, cmd: str) -> None:
    subprocess.run(["git", "-C", str(run_workspace), "tag", "v1"], check=True)
    (run_workspace / "hello.txt").write_text("dirty\n", encoding="utf-8")
    before = _git_refs(run_workspace)
    res = _exec(run_workspace, cmd)
    assert res.startswith("Refused:"), res
    assert _git_refs(run_workspace) == before
    assert (run_workspace / "hello.txt").read_text(encoding="utf-8") == "dirty\n"


@pytest.mark.parametrize("cmd", [
    "git branch", "git branch -a", "git branch -vv", "git branch --list ma*",
    "git branch --contains HEAD", "git branch --show-current",
    "git tag", "git tag -l", "git tag --list v*", "git tag --points-at HEAD",
    "git stash list", "git stash show", "git config --get user.name",
])
def test_git_read_only_forms_still_allowed(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert ok, reason


@pytest.mark.parametrize("flag", ["-fprint", "-fprint0", "-fls", "-delete", "-exec", "-execdir", "-ok", "-okdir"])
def test_find_write_and_exec_predicates_refused(run_workspace: Path, flag: str) -> None:
    out = run_workspace / "find_out.txt"
    res = _exec(run_workspace, f"find . -maxdepth 0 {flag} {out}")
    assert res.startswith("Refused:"), res
    assert not out.exists()


def test_find_fprintf_refused(run_workspace: Path) -> None:
    out = run_workspace / "find_out.txt"
    res = _exec(run_workspace, f"find . -maxdepth 0 -fprintf {out} %p")
    assert res.startswith("Refused:"), res
    assert not out.exists()


@pytest.fixture
def outside_secret(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    f = outside / "secret.txt"
    f.write_text("OUTSIDE_ROOT_CONTENT\n", encoding="utf-8")
    return f


def test_out_of_root_path_args_refused(run_workspace: Path, outside_secret: Path) -> None:
    (run_workspace / "link_out").symlink_to(outside_secret)
    (run_workspace / "dirlink").symlink_to(outside_secret.parent, target_is_directory=True)
    cmds = [
        f"cat {outside_secret}",
        "cat ../../outside/secret.txt",
        "cat link_out",
        "head -n 1 link_out",
        "tail link_out",
        "grep OUTSIDE link_out",
        f"rg OUTSIDE {outside_secret.parent}",
        "rg OUTSIDE dirlink",
        "ls dirlink",
        f"find {outside_secret.parent}",
        "stat link_out",
        "wc -l link_out",
        "diff hello.txt link_out",
        "cat ~/outside/secret.txt",
    ]
    for cmd in cmds:
        res = _exec(run_workspace, cmd)
        assert res.startswith("Refused:"), (cmd, res)
        assert "OUTSIDE_ROOT_CONTENT" not in res, cmd


def test_run_cat_secret_files_refused(run_workspace: Path) -> None:
    (run_workspace / ".env").write_text(f"GEMINI_API_KEY={FAKE_GOOGLE}\n", encoding="utf-8")
    (run_workspace / "config").mkdir()
    (run_workspace / "config" / "api_keys.json").write_text('{"k": "v"}', encoding="utf-8")
    (run_workspace / "notes.txt").symlink_to(run_workspace / ".env")
    for cmd in ["cat .env", "head .env", "grep KEY .env", "cat config/api_keys.json", "cat notes.txt"]:
        res = _exec(run_workspace, cmd)
        assert res.startswith("Refused:"), (cmd, res)
        assert FAKE_GOOGLE not in res


def test_redaction_covers_reviewed_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(os.environ):
        if k.endswith(("_API_KEY", "_TOKEN", "_AUTHKEY")):
            monkeypatch.delenv(k, raising=False)
    raw = (
        f"GEMINI_API_KEY={FAKE_GOOGLE}\n"
        f"TS_AUTHKEY={FAKE_TS}\n"
        f'{{"api_key": "{FAKE_PLAIN}"}}\n'
        f"OPENAI={FAKE_PROJ}\n"
        f"ANTHROPIC={FAKE_ANT}\n"
    )
    out = _redact_secrets(raw)
    for secret in (FAKE_GOOGLE, FAKE_TS, FAKE_PLAIN, FAKE_PROJ, FAKE_ANT):
        assert secret not in out
    assert "[REDACTED_GOOGLE_KEY]" in out
    assert "[REDACTED_TAILSCALE_KEY]" in out
    assert "[REDACTED_OPENAI_KEY]" in out
    assert "[REDACTED_ANTHROPIC_KEY]" in out
    assert "DEEPSEEK" not in out  # sk-proj / sk-ant are no longer labelled DeepSeek


def test_redaction_env_values_and_code_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gem-value-1234567890")
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "tok-value-1234567890")
    out = _redact_secrets("a gem-value-1234567890 b tok-value-1234567890")
    assert "gem-value" not in out and "tok-value" not in out
    code = "token = self.token\napi_key = load_key()\ntoken: string;"
    assert _redact_secrets(code) == code


class _FakeProc:
    def __init__(self, *args, **kwargs):
        _FakeProc.kwargs = kwargs
        self.pid = 999999
        self.returncode = 0

    def communicate(self, timeout=None):
        return "ok", ""


def test_child_env_strips_secrets_and_uses_process_group(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-" + "d" * 30)
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", "r" * 40)
    monkeypatch.setenv("TS_AUTHKEY", FAKE_TS)
    monkeypatch.setattr(run_mod.subprocess, "Popen", _FakeProc)
    res = _exec(run_workspace, "git status")
    assert "Exit: 0" in res
    env = _FakeProc.kwargs["env"]
    for k in ("DEEPSEEK_API_KEY", "KUREK_REMOTE_TOKEN", "TS_AUTHKEY"):
        assert k not in env
    assert env["GIT_PAGER"] == "cat"
    assert _FakeProc.kwargs["start_new_session"] is True


def test_kill_process_group_reaps_children() -> None:
    proc = subprocess.Popen(["sh", "-c", "sleep 30 & sleep 30; wait"], start_new_session=True)
    time.sleep(0.2)
    run_mod._kill_process_group(proc, signal.SIGKILL)
    assert proc.wait(timeout=5) != 0
    # the background child shared the group and must be gone as well
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("process group still alive after _kill_process_group")


def test_remote_blocks_codebase_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KUREK_REMOTE_ALLOW_EXEC", raising=False)
    engine = daemon.KurekEngine.__new__(daemon.KurekEngine)
    engine.actions = SimpleNamespace(run=MagicMock(return_value="executed"))
    blocked = engine._run_tool("codebase", {"action": "read", "path": ".env"}, remote=True)
    assert "disabled for remote" in blocked
    engine.actions.run.assert_not_called()
    assert "codebase" in daemon.REMOTE_BLOCKED_EXEC_TOOLS
    # local (non-remote) use is unaffected
    assert engine._run_tool("codebase", {"action": "status"}, remote=False) == "executed"


@pytest.mark.parametrize("cmd", [
    "grep -fOUTSIDE hello.txt",
    "grep --file=hello.txt hello.txt",
    "rg -f hello.txt x",
    "diff --diff-program=sh hello.txt hello.txt",
    "git config --file=x --list",
    "git config -f x --list",
    "git config --blob HEAD:x --list",
])
def test_round2_file_and_program_flags_refused(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert not ok and reason.startswith("Refused:"), cmd


def test_round2_attached_and_equals_path_values_checked(run_workspace: Path, outside_secret: Path) -> None:
    (run_workspace / ".env").write_text("X=1\n", encoding="utf-8")
    for cmd in [
        f"diff --from-file={outside_secret} hello.txt",
        f"diff --to-file={outside_secret} hello.txt",
        "diff --from-file=.env hello.txt",
        f"pytest {outside_secret.parent}",
        f"python -m pytest {outside_secret}",
        f"swift test --package-path {outside_secret.parent}",
        f"swift test --package-path={outside_secret.parent}",
    ]:
        res = _exec(run_workspace, cmd)
        assert res.startswith("Refused:"), (cmd, res)
        assert "OUTSIDE_ROOT_CONTENT" not in res


def test_redaction_quoted_values_without_digits() -> None:
    out = _redact_secrets('{"api_key": "abcdefghijk"}  password: "hunter-two-x"')
    assert "abcdefghijk" not in out and "hunter-two-x" not in out
