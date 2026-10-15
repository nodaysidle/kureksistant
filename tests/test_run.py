"""Tests for actions/run.py — sandboxed execution, allowlisting, refusals, audit logs, and redaction."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import kurek_daemon as daemon
from actions.run import run, _get_audit_log_path, _validate_command, _redact_secrets


# Tests that execute the real 'rg' binary; skipped where ripgrep is not installed.
needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep (rg) not installed")


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


# Fake key-shaped values are assembled at runtime so the packaging deny-list
# (scripts/package_release.sh) and push protection never see a key literal.
_FAKE_XAI_KEY = "xai-" + "secrettoken" + "1234567890abcdef"
_FAKE_SK_KEY = "sk-" + "999888777666" + "555444333222111"


def test_credential_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-FAKE_TEST_VALUE_not_a_real_key_00")
    monkeypatch.setenv("XAI_API_KEY", _FAKE_XAI_KEY)

    raw = (
        "Output containing sk-FAKE_TEST_VALUE_not_a_real_key_00 and "
        f"another key {_FAKE_SK_KEY} and "
        f"{_FAKE_XAI_KEY} and "
        "Bearer secret_bearer_token_1234567890 and "
        "clone https://user:supersecretpass@github.com/repo.git"
    )

    redacted = _redact_secrets(raw)
    assert "sk-abcdef" not in redacted
    assert "xai-secret" not in redacted
    assert _FAKE_SK_KEY not in redacted and _FAKE_XAI_KEY not in redacted
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


@needs_rg
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
    ok, _, reason = _validate_command(cmd, human_confirmed=True)
    assert ok, reason
    ok, _, reason = _validate_command(cmd)
    assert not ok and "confirmation" in reason


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
    ok, _, reason = _validate_command(cmd, human_confirmed=True)
    assert ok, reason
    ok, _, reason = _validate_command(cmd)
    assert not ok and "confirmation" in reason


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
        _FakeProc.args_seen = list(args[0]) if args else list(kwargs.get("args", []))
        self.pid = 999999
        self.returncode = 0
        r_out, w_out = os.pipe()
        r_err, w_err = os.pipe()
        os.write(w_out, b"ok")
        os.close(w_out)
        os.close(w_err)
        self.stdout = os.fdopen(r_out, "rb")
        self.stderr = os.fdopen(r_err, "rb")

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


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


@pytest.mark.parametrize("cmd", [
    "git show HEAD:.env",
    "git show HEAD:config/api_keys.json",
    "git log -p -- .env",
    "git diff HEAD~1 -- server.pem",
    "git blame .env",
])
def test_round3_git_object_secret_paths_refused(run_workspace: Path, cmd: str) -> None:
    res = _exec(run_workspace, cmd)
    assert res.startswith("Refused:"), (cmd, res)


def test_round3_git_repo_config_cannot_spawn_programs(run_workspace: Path) -> None:
    marker = run_workspace.parent / "fsmonitor_ran"
    hook = run_workspace.parent / "hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    subprocess.run(["git", "-C", str(run_workspace), "config", "core.fsmonitor", str(hook)], check=True)
    subprocess.run(["git", "-C", str(run_workspace), "config", "diff.external", str(hook)], check=True)
    (run_workspace / "hello.txt").write_text("changed\n", encoding="utf-8")
    for cmd in ("git status", "git diff", "git log -p -n 1"):
        res = _exec(run_workspace, cmd)
        assert "Exit: 0" in res, (cmd, res)
    assert not marker.exists()


# ---------------------------------------------------------------------------
# Round 4 regression tests: items (a)-(g)
# ---------------------------------------------------------------------------

from core import confirm as core_confirm


@pytest.mark.parametrize("cmd", [
    "git show :0:.env", "git show :1:.env", "git show :2:.env", "git show :3:.env",
    "git show :.env", "git show HEAD:config/api_keys.json",
    "git show :(top).env", "git log -p -- :(top).env", "git log -p -- :/.env",
    "git log -p -- :!main.py", "git log -p -- :^main.py", "git diff -- :(glob)**/.env",
    "git diff -- :(icase).ENV",
])
def test_a_git_stage_refs_and_pathspec_magic_refused(run_workspace: Path, cmd: str) -> None:
    res = _exec(run_workspace, cmd)
    assert res.startswith("Refused:"), (cmd, res)


def test_a_git_plain_object_paths_still_allowed(run_workspace: Path) -> None:
    res = _exec(run_workspace, "git show HEAD:hello.txt")
    assert "Exit: 0" in res and "Safe read file" in res


@pytest.mark.parametrize("cmd", [
    "rg --follow x .", "rg -L x .", "rg -nL x .", "grep -R x .", "grep -rR x .",
    "grep --dereference-recursive x .", "find -L .", "find . -follow", "find -H .",
    "ls -L .", "ls -lL .", "ls -H .", "stat -L hello.txt", "tree -l .", "tree -al .",
    "file -L hello.txt",
])
def test_c_symlink_following_flags_refused(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert not ok and reason.startswith("Refused:"), cmd


def test_c_grep_r_without_follow_allowed(run_workspace: Path) -> None:
    res = _exec(run_workspace, "grep -rn Safe .")
    assert "Exit: 0" in res and "hello.txt" in res


@pytest.mark.parametrize("cmd", ["make", "make -n", "make -j4 -k", "just", "just -n", "just --list test"])
def test_d_bare_make_and_just_refused(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert not ok and reason.startswith("Refused:"), cmd


@pytest.mark.parametrize("cmd", ["make test", "make -n build", "just test", "just --list"])
def test_d_explicit_targets_allowed(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd, human_confirmed=True)
    assert ok, reason


def test_e_git_blame_gets_no_textconv(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_mod, "_tracked_secret_files", lambda cwd: [])
    monkeypatch.setattr(run_mod.subprocess, "Popen", _FakeProc)
    _exec(run_workspace, "git blame hello.txt")
    argv = _FakeProc.args_seen
    assert argv[1] == "blame" and "--no-textconv" in argv
    _exec(run_workspace, "git diff HEAD")
    assert "--no-ext-diff" in _FakeProc.args_seen and "--no-textconv" in _FakeProc.args_seen
    env = _FakeProc.kwargs["env"]
    pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert pairs["core.fsmonitor"] == "false" and pairs["diff.external"] == "" and pairs["core.hooksPath"] == os.devnull


@pytest.mark.parametrize("cmd", [
    "git config --list", "git config -l", "git config --get-regexp .*", "git config --list --show-origin",
    "git config --get credential.helper", "git config --get http.https://x.extraheader",
    "git config --get url.https://tok@host/.insteadof", "git config --get-all github.token",
    "git config --get user.password",
])
def test_f_git_config_listing_and_credential_keys_refused(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert not ok and reason.startswith("Refused:"), cmd


@pytest.mark.parametrize("cmd", ["git config --get user.name", "git config --get remote.origin.url"])
def test_f_git_config_single_safe_key_allowed(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert ok, reason


@pytest.fixture
def hud(monkeypatch: pytest.MonkeyPatch):
    shown: list[tuple[str, str]] = []
    core_confirm.bind(lambda t, d: shown.append((t, d)), lambda: None)
    monkeypatch.setattr(core_confirm, "_pending", None)
    yield shown
    core_confirm.bind(None, None)
    core_confirm._pending = None


def _custom_make(ws: Path) -> Path:
    marker = ws / "custom_ran"
    (ws / "Makefile").write_text(f"deploy:\n\ttouch {marker}\n", encoding="utf-8")
    return marker


def test_g_model_confirm_param_is_ignored_and_schema_has_none(run_workspace: Path, hud) -> None:
    from actions.run import TOOL
    assert "confirm" not in TOOL["parameters"]["properties"]
    marker = _custom_make(run_workspace)
    res = run({"action": "exec", "cmd": "make deploy", "cwd": str(run_workspace), "confirm": True})
    assert "[CONFIRMATION_PENDING]" in res
    assert hud and hud[0][0] == "Run 'make deploy'"
    time.sleep(0.3)
    assert not marker.exists()  # model's confirm=True did not approve anything


def test_g_runs_only_after_human_yes(run_workspace: Path, hud) -> None:
    marker = _custom_make(run_workspace)
    res = _exec(run_workspace, "make deploy")
    assert "[CONFIRMATION_PENDING]" in res and core_confirm.is_pending()
    # the daemon's shortcut classifies the user's reply and resolves the gate
    assert daemon.classify_confirmation_reply("yes") is True
    core_confirm.resolve(True)
    deadline = time.time() + 10
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert marker.exists()
    listed = run({"action": "list"})
    assert "make deploy" in listed


def test_g_human_no_cancels(run_workspace: Path, hud) -> None:
    marker = _custom_make(run_workspace)
    _exec(run_workspace, "make deploy")
    core_confirm.resolve(False)
    time.sleep(0.3)
    assert not marker.exists() and not core_confirm.is_pending()


def test_g_no_interface_means_not_run(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    core_confirm.bind(None, None)
    monkeypatch.setattr(core_confirm, "_pending", None)
    marker = _custom_make(run_workspace)
    res = _exec(run_workspace, "make deploy")
    assert "cannot confirm" in res
    time.sleep(0.2)
    assert not marker.exists()


def test_g_second_request_does_not_stack(run_workspace: Path, hud) -> None:
    _custom_make(run_workspace)
    _exec(run_workspace, "make deploy")
    res = _exec(run_workspace, "make deploy")
    assert res.startswith("Refused:") and "already waiting" in res
    assert len(hud) == 1


def test_g_full_daemon_flow_yes_reply_runs_command(run_workspace: Path, hud) -> None:
    marker = _custom_make(run_workspace)
    _exec(run_workspace, "make deploy")
    engine = daemon.KurekEngine.__new__(daemon.KurekEngine)
    engine._cancel_requested = False
    reply = engine._handle_text_query_inner("yes", callback=None, mute_speaker=True)
    assert reply == "Confirmed and executed."
    deadline = time.time() + 10
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert marker.exists()


def test_a_git_glob_pathspec_is_literal(run_workspace: Path) -> None:
    secret = "glob" + "LeakValue" + "42"
    (run_workspace / ".envrc").write_text(f"TOKEN_VALUE_X={secret}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(run_workspace), "add", ".envrc"], check=True)
    subprocess.run(["git", "-C", str(run_workspace), "commit", "-qm", "envrc"], check=True)
    for cmd in ("git log -p -- .env*", "git show HEAD -- .e*", "git diff HEAD~1 -- *rc"):
        res = _exec(run_workspace, cmd)
        assert secret not in res, (cmd, res)


def test_c_tree_depth_flag_still_allowed() -> None:
    ok, _, reason = _validate_command("tree -L 2 .")
    assert ok, reason


def test_followup_tracked_secret_files_block_patch_output(run_workspace: Path) -> None:
    secret = "tracked" + "SecretValue" + "77"
    (run_workspace / ".env").write_text(f"X={secret}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(run_workspace), "add", "-f", ".env"], check=True)
    subprocess.run(["git", "-C", str(run_workspace), "commit", "-qm", "oops"], check=True)
    for cmd in ("git show HEAD", "git diff HEAD~1", "git log -p", "git log --patch -n 1",
                "git show --stat HEAD", "git log -L1,1:hello.txt"):
        res = _exec(run_workspace, cmd)
        assert res.startswith("Refused:"), (cmd, res)
        assert secret not in res
    res = _exec(run_workspace, "git log --oneline -n 2")
    assert "Exit: 0" in res and "oops" in res


def test_followup_patch_output_allowed_without_tracked_secrets(run_workspace: Path) -> None:
    res = _exec(run_workspace, "git show HEAD")
    assert "Exit: 0" in res and "Safe read file" in res



# ---------------------------------------------------------------------------
# Round 5 regression tests: items (1)-(5)
# ---------------------------------------------------------------------------


@needs_rg
def test_r5_1_rg_ignores_ripgrep_config(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    victim = run_workspace / "victim.txt"
    victim.write_text("x\n", encoding="utf-8")
    cfg = run_workspace.parent / "rgrc"
    cfg.write_text("--pre=rm\n", encoding="utf-8")
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", str(cfg))
    res = _exec(run_workspace, "rg x victim.txt")
    assert "Exit: 0" in res, res
    assert victim.exists()


def test_r5_1_rg_child_gets_no_config(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", "/nonexistent/rgrc")
    monkeypatch.setattr(run_mod.subprocess, "Popen", _FakeProc)
    _exec(run_workspace, "rg Safe hello.txt")
    assert _FakeProc.args_seen[1] == "--no-config"
    assert "RIPGREP_CONFIG_PATH" not in _FakeProc.kwargs["env"]


def test_r5_2_tracked_secret_check_uses_repo_root(run_workspace: Path) -> None:
    secret = "root" + "TrackedSecret" + "55"
    (run_workspace / ".env").write_text(f"X={secret}\n", encoding="utf-8")
    sub = run_workspace / "pkg"
    sub.mkdir()
    (sub / "mod.txt").write_text("m\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(run_workspace), "add", "-f", ".env", "pkg/mod.txt"], check=True)
    subprocess.run(["git", "-C", str(run_workspace), "commit", "-qm", "root secret"], check=True)
    for cmd in ("git show HEAD", "git log -p -n 1", "git diff HEAD~1"):
        res = _exec(sub, cmd)
        assert res.startswith("Refused:") and ".env" in res, (cmd, res)
        assert secret not in res


@pytest.mark.parametrize("cmd", [
    "git diff --textconv", "git log -p --textconv", "git show --textconv HEAD",
    "git diff --textconv=yes", "git blame --textconv hello.txt", "git log --no-textconv --textconv",
])
def test_r5_3_textconv_refused_anywhere(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert not ok and "textconv" in reason, cmd


@pytest.mark.parametrize("cmd", [
    "file -C", "file -Cm/tmp/magic", "file -m/tmp/magic hello.txt", "file -f/tmp/list",
    "file -bm/tmp/magic hello.txt", "file -bC", "file --magic-file=/tmp/m hello.txt",
    "file --compile", "file --files-from=/tmp/list",
])
def test_r5_4_file_attached_risky_flags_refused(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert not ok and reason.startswith("Refused:"), cmd


def test_r5_4_file_plain_still_allowed(run_workspace: Path) -> None:
    ok, _, reason = _validate_command("file -b hello.txt")
    assert ok, reason



# ---------------------------------------------------------------------------
# Round 6 regression tests: items (1)-(4)
# ---------------------------------------------------------------------------


def test_r6_1_child_env_is_an_allowlist(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA" + "X" * 16)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "s" * 40)
    monkeypatch.setenv("SOME_RANDOM_CREDENTIAL", "c" * 20)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("CARGO_HOME", "/tmp/cargo-home")
    monkeypatch.setattr(run_mod.subprocess, "Popen", _FakeProc)
    res = _exec(run_workspace, "git status")
    assert "Exit: 0" in res, res
    env = _FakeProc.kwargs["env"]
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "SOME_RANDOM_CREDENTIAL", "DATABASE_URL"):
        assert k not in env, k
    assert env["LC_ALL"] == "C" and env["CARGO_HOME"] == "/tmp/cargo-home"
    assert "PATH" in env and env["HOME"]
    # git safety env is still injected
    assert env["GIT_PAGER"] == "cat" and int(env["GIT_CONFIG_COUNT"]) > 0
    allowed_extra = {"GIT_PAGER", "PAGER", "CI", "TERM", "NO_COLOR", "CARGO_TERM_COLOR", "npm_config_yes",
                     "DEBIAN_FRONTEND", "GIT_TERMINAL_PROMPT", "GIT_LITERAL_PATHSPECS", "GIT_CONFIG_COUNT"}
    for k in env:
        assert (k in run_mod._CHILD_ENV_ALLOW or k.startswith("LC_") or k in allowed_extra
                or k.startswith("GIT_CONFIG_KEY_") or k.startswith("GIT_CONFIG_VALUE_")), k


def test_r6_1_real_child_cannot_print_aws_secret(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "aws" + "SecretValue" + "123456"
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", secret)
    proc_env = run_mod._child_env_base()
    assert "AWS_SECRET_ACCESS_KEY" not in proc_env


def _commit(ws: Path, msg: str, *paths: str, force: bool = True) -> None:
    present = [p for p in paths if (ws / p).exists()]
    if present:
        subprocess.run(["git", "-C", str(ws), "add", *(["-f"] if force else []), *present], check=True)
    gone = [p for p in paths if not (ws / p).exists()]
    if gone:
        subprocess.run(["git", "-C", str(ws), "rm", "-q", "--cached", "--ignore-unmatch", *gone], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", msg], check=True)


def test_r6_2_deleted_secret_in_history_blocks_patch_output(run_workspace: Path) -> None:
    secret = "deleted" + "SecretValue" + "88"
    (run_workspace / ".env").write_text(f"X={secret}\n", encoding="utf-8")
    _commit(run_workspace, "add env", ".env")
    (run_workspace / ".env").unlink()
    _commit(run_workspace, "remove env", ".env")
    assert run_mod._tracked_secret_files(run_workspace) == []  # no longer tracked
    for cmd in ("git show HEAD", "git show HEAD~1", "git log -p", "git log --patch -n 2",
                "git diff HEAD~2 HEAD~1", "git diff HEAD~1..HEAD", "git log -p HEAD~2..HEAD",
                "git log -m -p", "git show --stat HEAD"):
        res = _exec(run_workspace, cmd)
        assert res.startswith("Refused:") and ".env" in res, (cmd, res)
        assert secret not in res


def test_r6_2_renamed_secret_blocks_patch_output(run_workspace: Path) -> None:
    secret = "renamed" + "SecretValue" + "66"
    (run_workspace / ".env").write_text(f"X={secret}\n", encoding="utf-8")
    _commit(run_workspace, "add env", ".env")
    subprocess.run(["git", "-C", str(run_workspace), "mv", ".env", "notes.txt"], check=True)
    _commit(run_workspace, "rename", ".env", "notes.txt")
    res = _exec(run_workspace, "git show HEAD")
    assert res.startswith("Refused:") and secret not in res, res


def test_r6_2_stash_with_secret_blocks_stash_patch(run_workspace: Path) -> None:
    secret = "stash" + "SecretValue" + "44"
    (run_workspace / ".env").write_text(f"X={secret}\n", encoding="utf-8")
    (run_workspace / "hello.txt").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(run_workspace), "stash", "push", "-q", "--include-untracked"], check=True)
    for cmd in ("git stash show -p", "git stash show -p -u", "git stash list -p"):
        res = _exec(run_workspace, cmd)
        assert res.startswith("Refused:"), (cmd, res)
        assert secret not in res


def test_r6_2_clean_history_still_allows_patches(run_workspace: Path) -> None:
    (run_workspace / "hello.txt").write_text("v2\n", encoding="utf-8")
    _commit(run_workspace, "v2", "hello.txt", force=False)
    for cmd in ("git show HEAD", "git log -p -n 1", "git diff HEAD~1 HEAD"):
        res = _exec(run_workspace, cmd)
        assert "Exit: 0" in res and "v2" in res, (cmd, res)


@pytest.mark.parametrize("cmd", [
    "pytest", "pytest -q tests", "python -m pytest", "python3 -m pytest -x", "python -m unittest",
    "npm test", "pnpm test", "yarn test", "bun test", "npm run test", "pnpm run build",
    "go test ./...", "cargo test", "cargo check", "cargo build", "cargo clippy", "cargo bench",
    "make test", "make check", "just test", "swift test", "swift build",
])
def test_r6_3_test_runners_need_human_confirmation(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert not ok and "confirmation" in reason, (cmd, reason)
    ok, _, reason = _validate_command(cmd, human_confirmed=True)
    assert ok, (cmd, reason)


def test_r6_3_pytest_conftest_runs_only_after_yes(run_workspace: Path, hud) -> None:
    marker = run_workspace / "conftest_ran"
    (run_workspace / "conftest.py").write_text(
        f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    (run_workspace / "test_x.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    res = run({"action": "exec", "cmd": "pytest -q -p no:cacheprovider", "cwd": str(run_workspace), "confirm": True})
    assert "[CONFIRMATION_PENDING]" in res, res
    time.sleep(0.5)
    assert not marker.exists()  # model's confirm=True approved nothing
    core_confirm.resolve(False)
    time.sleep(0.3)
    assert not marker.exists()


def test_r6_3_version_queries_need_no_confirmation() -> None:
    for cmd in ("cargo --version", "npm --version", "go version", "python --version", "just --list"):
        ok, _, reason = _validate_command(cmd)
        assert ok, (cmd, reason)


def test_r6_4_output_cap_kills_process_group(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_mod, "_MAX_OUTPUT_BYTES", 4096)
    proc = subprocess.Popen(["sh", "-c", "yes kurek & yes kurek; wait"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, start_new_session=True)
    t0 = time.time()
    out, err, outcome = run_mod._stream_output(proc, timeout=30)
    assert outcome == "capped"
    assert len(out.encode()) + len(err.encode()) <= 4096
    assert time.time() - t0 < 10
    assert proc.poll() is not None
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("process group still alive after output cap")


def test_r6_4_exec_reports_output_limit(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_mod, "_MAX_OUTPUT_BYTES", 2048)
    big = run_workspace / "big.txt"
    big.write_text("line of text\n" * 20000, encoding="utf-8")
    res = _exec(run_workspace, "cat big.txt")
    assert "OUTPUT LIMIT" in res, res[:300]
    assert len(res) < 20000


def test_r6_4_timeout_still_kills_group(run_workspace: Path) -> None:
    proc = subprocess.Popen(["sh", "-c", "sleep 30 & sleep 30; wait"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, start_new_session=True)
    t0 = time.time()
    _, _, outcome = run_mod._stream_output(proc, timeout=1)
    assert outcome == "timeout" and time.time() - t0 < 8
    assert proc.poll() is not None


def test_r6_4_normal_output_unchanged(run_workspace: Path) -> None:
    res = _exec(run_workspace, "cat hello.txt")
    assert "Exit: 0" in res and "Safe read file line 1" in res


# ---------------------------------------------------------------------------
# Round 7 regression tests: registry auth files in run
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [".docker/config.json", ".config/containers/auth.json", ".dockercfg", ".npmrc"])
def test_r7_1_run_refuses_registry_auth_files(run_workspace: Path, rel: str) -> None:
    value = "cmVn" + "UnVuVmFsdWU" + "5OQ=="
    f = run_workspace / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('{"auths": {"x": {"auth": "%s"}}}\n' % value, encoding="utf-8")
    for cmd in (f"cat {rel}", f"head {rel}", f"grep auth {rel}"):
        res = _exec(run_workspace, cmd)
        assert res.startswith("Refused:") and value not in res, (cmd, res)
    subprocess.run(["git", "-C", str(run_workspace), "add", "-f", rel], check=True)
    subprocess.run(["git", "-C", str(run_workspace), "commit", "-qm", "reg auth"], check=True)
    res = _exec(run_workspace, "git show HEAD")
    assert res.startswith("Refused:") and value not in res, res


def test_r7_1_run_output_redacts_registry_auth(run_workspace: Path) -> None:
    (run_workspace / "notes.txt").write_text('{"auth": "c2VjcmV0dmFsdWU=", "password": "pw1"}\n', encoding="utf-8")
    res = _exec(run_workspace, "cat notes.txt")
    assert "Exit: 0" in res and "c2VjcmV0dmFsdWU=" not in res and '"pw1"' not in res


# ---------------------------------------------------------------------------
# Round 8 regression tests: (1) recursive search excludes, (4) redaction
# ---------------------------------------------------------------------------

_R8_SECRET = "r8" + "LeakMarker" + "Q7"


@pytest.fixture
def secret_tree(run_workspace: Path) -> Path:
    files = [".env", ".env.local", "api_keys.json", "config/api_keys.json", "server.pem", "tls.key",
             "id_ed25519", ".docker/config.json", ".config/containers/auth.json", ".npmrc", ".pypirc",
             ".dockercfg", ".cargo/credentials.toml", ".gem/credentials", ".git-credentials", ".netrc"]
    for rel in files:
        f = run_workspace / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"value={_R8_SECRET}\n", encoding="utf-8")
    (run_workspace / "visible.txt").write_text(f"{_R8_SECRET} public\n", encoding="utf-8")
    return run_workspace


@pytest.mark.parametrize("cmd", [
    *[pytest.param(c, marks=needs_rg) for c in (
        f"rg {_R8_SECRET}", f"rg --hidden --no-ignore {_R8_SECRET} .", f"rg -uuu {_R8_SECRET}",
        f"rg -uu --hidden -g .env {_R8_SECRET} .", f"rg --no-ignore-vcs --hidden {_R8_SECRET} -- .",
        f"rg -l --hidden --no-ignore {_R8_SECRET}", "rg --files --hidden --no-ignore",
    )],
    f"grep -rn {_R8_SECRET} .", f"grep -r --exclude=visible.txt {_R8_SECRET} .", f"grep -rl {_R8_SECRET} .",
])
def test_r8_1_recursive_search_never_prints_secret_files(secret_tree: Path, cmd: str) -> None:
    res = _exec(secret_tree, cmd)
    if res.startswith("Refused:"):
        assert _R8_SECRET not in res.split("\n", 1)[0].replace(cmd, "")
        return
    assert "Exit:" in res, (cmd, res)
    for name in (".env", "api_keys.json", "server.pem", "tls.key", "id_ed25519", "config.json",
                 "auth.json", ".npmrc", ".pypirc", ".dockercfg", "credentials", ".git-credentials", ".netrc"):
        for line in res.splitlines():
            if _R8_SECRET in line or line.strip().endswith(name):
                assert name not in line.split(":")[0].split("/")[-1] or "visible" in line, (cmd, line)
    assert res.count(_R8_SECRET) <= 2  # only visible.txt (and the echoed command line)


def test_r8_1_excludes_are_appended_last(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_mod.subprocess, "Popen", _FakeProc)
    _exec(run_workspace, "rg -uuu -g .env x .")
    argv = _FakeProc.args_seen
    assert argv[1] == "--no-config"
    last_user = argv.index(".")
    assert "!.env*" in argv[last_user:]
    _exec(run_workspace, "grep -r x -- .")
    argv = _FakeProc.args_seen
    assert argv.index("--") > argv.index("--exclude=.env*")


@pytest.mark.parametrize("cmd", [
    "grep -r --include=.env x .", "grep -r --include .env x .", "grep -r --exclude-from=list x .",
    "diff -r . /tmp", "diff -ru a b", "diff --recursive a b",
])
def test_r8_1_risky_override_flags_refused(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert not ok and reason.startswith("Refused:"), cmd


def test_r8_1_search_inside_git_or_docker_dir_refused(secret_tree: Path) -> None:
    (secret_tree / ".git" / "config").exists()
    for sub, cmd in ((".git", "rg url ."), (".docker", f"rg {_R8_SECRET} ."), (".", "grep -r x .git"),
                     (".", f"rg {_R8_SECRET} .docker")):
        res = _exec(secret_tree / sub if sub != "." else secret_tree, cmd)
        assert res.startswith("Refused:") and _R8_SECRET not in res, (sub, cmd, res)


def test_r8_1_direct_secret_target_still_refused(secret_tree: Path) -> None:
    for cmd in (f"rg {_R8_SECRET} .env", f"grep {_R8_SECRET} .docker/config.json", "rg x api_keys.json"):
        res = _exec(secret_tree, cmd)
        assert res.startswith("Refused:") and _R8_SECRET not in res, (cmd, res)


def test_r8_1_dynamic_registry_auth_name_excluded(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = run_workspace / "my-registry-login.json"
    custom.write_text(f'{{"x": "{_R8_SECRET}"}}\n', encoding="utf-8")
    monkeypatch.setenv("REGISTRY_AUTH_FILE", str(custom))
    for cmd in (f"rg --hidden --no-ignore {_R8_SECRET}", f"grep -rn {_R8_SECRET} ."):
        res = _exec(run_workspace, cmd)
        assert "my-registry-login.json" not in res, (cmd, res)


def test_r8_4_redaction_aws_and_token_prefixes() -> None:
    samples = {
        "AKIA" + "ABCDEFGHIJKLMNOP": "AWS_KEY_ID",
        "ASIA" + "QRSTUVWXYZ234567": "AWS_KEY_ID",
        "ghp_" + "a" * 36: "GH_TOKEN",
        "gho_" + "b" * 36: "GH_TOKEN",
        "github_pat_" + "c" * 40: "GH_TOKEN",
        "glpat-" + "d" * 20: "GITLAB",
        "xoxb-" + "1" * 12 + "-abc": "SLACK",
        "xoxp-" + "2" * 12: "SLACK",
        "sk_live_" + "e" * 24: "STRIPE",
        "rk_live_" + "f" * 24: "STRIPE",
        "hf_" + "g" * 34: "HF_TOKEN",
        "npm_" + "h" * 36: "NPM_TOKEN",
    }
    for value, label in samples.items():
        out = _redact_secrets(f"x {value} y")
        assert value not in out and label in out, (value, out)
    aws_secret = "wJalr" + "XUtnFEMI/K7MDENG/bPxRfiCY" + "EXAMPLEKEY"
    for line in (f"aws_secret_access_key = {aws_secret}", f'"AWS_SECRET_ACCESS_KEY": "{aws_secret}"',
                 f"AWS_SECRET_ACCESS_KEY={aws_secret}"):
        assert aws_secret not in _redact_secrets(line), line
    code = "from huggingface_hub import hf_hub_download\nnpm_config_yes = 1\nAKIA_PREFIX = 'AKIA'\n"
    assert _redact_secrets(code) == code


def test_r8_4_run_output_redacts_aws_key(run_workspace: Path) -> None:
    key = "AKIA" + "ZYXWVUTSRQPONMLK"
    (run_workspace / "notes.txt").write_text(f"key {key}\n", encoding="utf-8")
    res = _exec(run_workspace, "cat notes.txt")
    assert "Exit: 0" in res and key not in res


# ---------------------------------------------------------------------------
# Round 9 regression tests: (1) cargo always confirmed, (2) dynamic patch guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "cargo check", "cargo metadata", "cargo tree", "cargo version", "cargo test", "cargo build",
    "cargo clippy", "cargo bench", "cargo tree --depth 1", "cargo metadata --format-version 1",
])
def test_r9_1_every_cargo_subcommand_needs_yes(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert not ok and "confirmation" in reason, (cmd, reason)
    ok, _, reason = _validate_command(cmd, human_confirmed=True)
    assert ok, (cmd, reason)


@pytest.mark.parametrize("cmd", [
    "cargo status", "cargo payload", "cargo b", "cargo t", "cargo r", "cargo run", "cargo x --version",
    "cargo -q check", "cargo --list", "cargo install foo",
])
def test_r9_1_unknown_or_alias_cargo_subcommands_refused_even_if_confirmed(cmd: str) -> None:
    for confirmed in (False, True):
        ok, _, reason = _validate_command(cmd, human_confirmed=confirmed)
        assert not ok and reason.startswith("Refused:"), (cmd, confirmed, reason)


@pytest.mark.parametrize("cmd", ["cargo --version", "cargo -V", "cargo --help", "cargo -h"])
def test_r9_1_cargo_version_help_need_no_yes(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert ok, reason


def test_r9_1_repo_alias_cargo_status_does_not_run(run_workspace: Path, hud) -> None:
    marker = run_workspace / "alias_ran"
    (run_workspace / ".cargo").mkdir()
    (run_workspace / ".cargo" / "config.toml").write_text(
        f'[alias]\nstatus = "run --bin touch -- {marker}"\n', encoding="utf-8")
    res = _exec(run_workspace, "cargo status")
    assert res.startswith("Refused:"), res
    res = _exec(run_workspace, "cargo metadata")
    assert "[CONFIRMATION_PENDING]" in res or "cannot confirm" in res, res
    core_confirm.resolve(False)
    time.sleep(0.2)
    assert not marker.exists()


def test_r9_2_custom_registry_auth_file_blocks_patch(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "custom" + "RegAuth" + "Value31"
    sub = run_workspace / "deploy"
    sub.mkdir()
    custom = sub / "login-state.json"
    custom.write_text(f'{{"x": "{secret}"}}\n', encoding="utf-8")
    _commit(run_workspace, "custom auth", "deploy/login-state.json")
    monkeypatch.setenv("REGISTRY_AUTH_FILE", str(custom))
    for cwd in (run_workspace, sub):
        for cmd in ("git show HEAD", "git log -p -n 1", "git diff HEAD~1 HEAD", "git log -p --relative -n 1"):
            res = _exec(cwd, cmd)
            assert res.startswith("Refused:") and secret not in res, (cwd, cmd, res)


def test_r9_2_deleted_and_renamed_custom_auth_file_blocks_patch(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "moved" + "RegAuth" + "Value32"
    dcfg = run_workspace / "dockercfg"
    dcfg.mkdir()
    (dcfg / "config.json").write_text(f'{{"x": "{secret}"}}\n', encoding="utf-8")
    _commit(run_workspace, "docker cfg", "dockercfg/config.json")
    monkeypatch.setenv("DOCKER_CONFIG", str(dcfg))
    subprocess.run(["git", "-C", str(run_workspace), "mv", "dockercfg/config.json", "notes.json"], check=True)
    _commit(run_workspace, "rename", "dockercfg/config.json", "notes.json")
    (run_workspace / "notes.json").unlink()
    _commit(run_workspace, "delete", "notes.json")
    for cmd in ("git show HEAD~1", "git log -p -n 3", "git diff HEAD~3 HEAD~1"):
        res = _exec(run_workspace, cmd)
        assert res.startswith("Refused:") and secret not in res, (cmd, res)


def test_r9_2_dynamic_guard_does_not_block_unrelated_history(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGISTRY_AUTH_FILE", str(run_workspace / "never-committed.json"))
    res = _exec(run_workspace, "git show HEAD")
    assert "Exit: 0" in res and "Safe read file" in res


def test_r9_2_diff_across_rename_of_static_secret_blocks(run_workspace: Path) -> None:
    secret = "static" + "Renamed" + "Value33"
    (run_workspace / ".env").write_text(f"X={secret}\n", encoding="utf-8")
    _commit(run_workspace, "env", ".env")
    subprocess.run(["git", "-C", str(run_workspace), "mv", ".env", "notes.txt"], check=True)
    _commit(run_workspace, "rename", ".env", "notes.txt")
    for cmd in ("git diff HEAD~2 HEAD", "git diff HEAD~2..HEAD", "git diff HEAD~2...HEAD", "git diff HEAD~2"):
        res = _exec(run_workspace, cmd)
        assert res.startswith("Refused:") and secret not in res, (cmd, res)


# ---------------------------------------------------------------------------
# Round 11: binaries resolve only from trusted dirs; secret-named keys redact any value
# ---------------------------------------------------------------------------

import actions.codebase as _cb  # noqa: E402
from actions.codebase import resolve_trusted_binary  # noqa: E402


def _fake_bin(d: Path, name: str, marker: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text(f"#!/bin/sh\ntouch {marker}\necho HIJACKED\n", encoding="utf-8")
    f.chmod(0o755)
    return f


def test_r11_project_bin_on_path_is_never_executed(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = run_workspace.parent / "hijacked"
    _fake_bin(run_workspace / "bin", "cat", marker)
    monkeypatch.setenv("PATH", f"{run_workspace / 'bin'}:/usr/bin:/bin")
    res = _exec(run_workspace, "cat hello.txt")
    assert "Safe read file" in res and "HIJACKED" not in res
    assert not marker.exists()
    path, _ = resolve_trusted_binary("cat")
    assert path and Path(path).parent.as_posix() in _cb.SYSTEM_BIN_DIRS


@needs_rg
def test_r11_project_rg_on_path_is_never_executed(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = run_workspace.parent / "hijacked_rg"
    _fake_bin(run_workspace / "tools", "rg", marker)
    monkeypatch.setenv("PATH", f"{run_workspace / 'tools'}:/usr/bin:/bin")
    res = _exec(run_workspace, "rg -n Safe hello.txt")
    assert "Exit: 0" in res and "Safe read file" in res and "HIJACKED" not in res
    assert not marker.exists()


def test_r11_relative_path_entry_and_paths_refused(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = run_workspace.parent / "hijacked_rel"
    _fake_bin(run_workspace / "bin", "cat", marker)
    monkeypatch.chdir(run_workspace)
    monkeypatch.setenv("PATH", "bin:.:/usr/bin:/bin")
    res = _exec(run_workspace, "cat hello.txt")
    assert "Safe read file" in res and not marker.exists()
    for name in ("bin/cat", "./cat", "/usr/bin/cat", ".."):
        path, why = resolve_trusted_binary(name)
        assert path is None and why.startswith("Refused"), name


def test_r11_trusted_dir_symlink_into_project_refused(
        run_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = run_workspace.parent / "hijacked_link"
    fake = _fake_bin(run_workspace / ".hidden", "cat", marker)
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir()
    (sysbin / "cat").symlink_to(fake)
    monkeypatch.setattr(_cb, "SYSTEM_BIN_DIRS", (str(sysbin),))
    res = _exec(run_workspace, "cat hello.txt")
    assert res.startswith("Refused") and "project root" in res
    assert not marker.exists()
    # a symlink whose target lies outside every trusted dir is refused too
    other = _fake_bin(tmp_path / "elsewhere", "cat", marker)
    (sysbin / "cat").unlink()
    (sysbin / "cat").symlink_to(other)
    path, why = resolve_trusted_binary("cat")
    assert path is None and "outside the trusted" in why
    res = _exec(run_workspace, "cat hello.txt")
    assert res.startswith("Refused") and not marker.exists()


def test_r11_user_toolchain_dirs_only_for_confirmed_tools(
        run_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # HOME is tmp_path (fixture), outside the project root run_workspace.
    marker = tmp_path / "user_cat_ran"
    local_bin = tmp_path / ".local" / "bin"
    _fake_bin(local_bin, "cat", marker)
    _fake_bin(local_bin, "kurek-only-user-tool", marker)
    local_bin.chmod(0o755)  # independent of the runner's umask (world-writable dirs are refused)
    monkeypatch.setenv("PATH", f"{local_bin}:/usr/bin:/bin")
    # no-confirm read tools: system dirs only
    path, _ = resolve_trusted_binary("cat")
    assert path and Path(path).parent.as_posix() in _cb.SYSTEM_BIN_DIRS
    assert resolve_trusted_binary("kurek-only-user-tool")[0] is None
    # confirm-gated commands may use ~/.local/bin (PATH order kept)
    assert resolve_trusted_binary("cat", allow_user_toolchains=True)[0] == str(local_bin / "cat")
    assert resolve_trusted_binary("kurek-only-user-tool", allow_user_toolchains=True)[0]
    # ...but not when that dir is inside an allowed project root
    monkeypatch.setenv("KUREK_CODEBASE_ROOTS", str(tmp_path))
    assert resolve_trusted_binary("kurek-only-user-tool", allow_user_toolchains=True)[0] is None
    path, _ = resolve_trusted_binary("cat", allow_user_toolchains=True)
    assert path and Path(path).parent.as_posix() in _cb.SYSTEM_BIN_DIRS
    assert not marker.exists()


def test_r11_confirmed_make_still_runs_from_system_dir(run_workspace: Path, hud) -> None:
    marker = _custom_make(run_workspace)
    if resolve_trusted_binary("make", allow_user_toolchains=True)[0] is None:
        pytest.skip("make not installed in a trusted dir")
    res = _exec(run_workspace, "make deploy")
    assert "[CONFIRMATION_PENDING]" in res
    core_confirm.resolve(True)
    deadline = time.time() + 10
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert marker.exists()


@pytest.mark.parametrize("line", [
    "PASSWORD=hunter-two",
    "DB_PASSWORD=hunter-two",
    "export db_passwd=letmein",
    "Client_Secret=abcdef",
    "password: hunter-two",
    "api_key=xyz",
    "APIKEY: shortkey",
    "--token=abc",
    "https://x.example/cb?token=abc&next=1",
    "PASSWORD=hunter.two",
    "Auth: letmein",
    "aws_credentials=foo-bar",
    "PRIVATE_KEY=abc",
    'password = "pw"',
    '{"secret": "s3"}',
])
def test_r11_secret_named_keys_redact_any_value(line: str) -> None:
    out = _redact_secrets(line)
    assert "[REDACTED" in out, (line, out)
    for leaked in ("hunter-two", "letmein", "abcdef", "xyz", "shortkey", "=abc", "hunter.two", "foo-bar",
                   '"pw"', '"s3"'):
        if leaked in line:
            assert leaked not in out, (line, out)


@pytest.mark.parametrize("code", [
    "token = self.token",
    "token=self.token",
    "key = config.api_key",
    "password=None",
    "password=null",
    'password=""',
    "PASSWORD_MIN_LEN=8",
    "TOKEN_URL=https://example.invalid/oauth",
    "max_tokens=4096",
    "author=Alan",
    "if password:",
    "if not password: raise ValueError('x')",
    "api_key = load_key()",
    "token: string;",
    "password: Optional[str] = None",
    "connect(host, password=password)",
    '{"token": token}',
    "if password == other:",
    "(password := getpass())",
    'let token = env::var("T")',
    "secret: Option<String>,",
])
def test_r11_code_and_non_values_stay_readable(code: str) -> None:
    assert _redact_secrets(code) == code


def test_r11_missing_binary_still_asks_then_refuses_after_yes(
        run_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hud) -> None:
    # e.g. CI: pytest lives in the runner's toolcache, outside every trusted dir
    marker = _custom_make(run_workspace)
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setattr(_cb, "SYSTEM_BIN_DIRS", (str(empty),))
    monkeypatch.setattr(_cb, "_user_toolchain_dirs", lambda: [])
    res = _exec(run_workspace, "make deploy")
    assert "[CONFIRMATION_PENDING]" in res, res
    core_confirm.resolve(True)
    time.sleep(0.5)
    assert not marker.exists()
    assert "not found in the trusted binary dirs" in run({"action": "read", "run_id": res.split("run id ")[1].split(";")[0]})


# ---------------------------------------------------------------------------
# Round 12: go build/vet confirmed; -vettool/-o blocked; audit log hardening
# ---------------------------------------------------------------------------

import stat  # noqa: E402

_R12_PASSWORD = "hunter-" + "two"


@pytest.mark.parametrize("cmd", ["go build", "go build ./...", "go vet ./...", "go vet ."])
def test_r12_1_go_build_and_vet_need_confirmation(cmd: str) -> None:
    ok, _, reason = _validate_command(cmd)
    assert not ok and "confirmation" in reason, (cmd, reason)
    ok, _, reason = _validate_command(cmd, human_confirmed=True)
    assert ok, (cmd, reason)


@pytest.mark.parametrize("cmd", [
    "go vet -vettool=/tmp/evil.so ./...",
    "go vet -vettool /tmp/evil.so ./...",
    "go build -o=/tmp/out ./...",
    "go build -o /tmp/out ./...",
])
def test_r12_1_go_vettool_and_output_flags_refused(cmd: str) -> None:
    for confirmed in (False, True):
        ok, _, reason = _validate_command(cmd, human_confirmed=confirmed)
        assert not ok and reason.startswith("Refused:"), (cmd, confirmed, reason)


def test_r12_1_go_vettool_refused_end_to_end(run_workspace: Path) -> None:
    res = _exec(run_workspace, "go vet -vettool=/tmp/evil.so ./...")
    assert res.startswith("Refused:") and "vettool" in res, res


def test_r12_2_audit_log_redacts_secret(run_workspace: Path) -> None:
    audit_file = _get_audit_log_path()
    if audit_file.exists():
        audit_file.unlink()
    res = _exec(run_workspace, f"rg --pre=rm x PASSWORD={_R12_PASSWORD} hello.txt")
    assert res.startswith("Refused:"), res
    content = audit_file.read_text(encoding="utf-8")
    assert "PASSWORD=" in content, content
    assert "[REDACTED_SECRET]" in content, content
    assert _R12_PASSWORD not in content, content


def test_r12_2_audit_log_file_is_0600_and_dir_is_0700(run_workspace: Path) -> None:
    audit_file = _get_audit_log_path()
    if audit_file.exists():
        audit_file.unlink()
    _exec(run_workspace, "cat hello.txt")
    assert audit_file.exists()
    assert stat.S_IMODE(audit_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(audit_file.parent.stat().st_mode) == 0o700


def test_r12_3_run_history_capped(run_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_mod, "_RUN_HISTORY_MAX", 5)
    for _ in range(8):
        _exec(run_workspace, "cat hello.txt")
    assert len(run_mod._RUN_HISTORY) <= 5, len(run_mod._RUN_HISTORY)
