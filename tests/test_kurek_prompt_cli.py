"""`kurek prompt` reports a prompt that was not sent.

Safety: this never reaches the live daemon. The test runs a copy of bin/kurek from a
pytest temp dir, and the env dict replaces the whole environment: HOME, KUREK_DIR and
XDG_RUNTIME_DIR are empty temp dirs, so no kurek-trigger is found (asserted before each
run), and PATH holds only stub executables (curl, pgrep, systemctl, notify-send, python3,
dirname). The stubbed pgrep exits 0, so ensure_daemon returns before any probe, and the
stubbed curl only records its arguments.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[1] / "bin" / "kurek"
BASH = shutil.which("bash")

_STUBS = {
    "pgrep": "exit 0",
    "systemctl": "exit 1",
    "notify-send": "exit 0",
    # Called as: python3 -c <code> <prompt>; print the prompt as a JSON string.
    "python3": 'printf \'"%s"\\n\' "$3"',
    "dirname": 'printf \'%s\\n\' "${1%/*}"',
}


def _stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


@pytest.fixture
def prompt(tmp_path: Path):
    stubs, home, kurek_dir, runtime, cli_dir = (
        tmp_path / d for d in ("stubs", "home", "kurek_dir", "runtime", "cli")
    )
    for d in (stubs, home, kurek_dir, runtime, cli_dir):
        d.mkdir()
    for name, body in _STUBS.items():
        _stub(stubs / name, body)
    cli = cli_dir / "kurek"
    shutil.copy(CLI, cli)
    args_log = tmp_path / "curl-args.txt"

    def run(curl_exit: int):
        _stub(stubs / "curl", f'printf "%s\\n" "$@" > "{args_log}"\nexit {curl_exit}')
        for d in (stubs, home, kurek_dir, runtime, cli_dir):
            assert not any(p.name == "kurek-trigger" for p in d.rglob("*")), d
        done = subprocess.run(
            [BASH, str(cli), "prompt", "hello there"], capture_output=True, text=True, timeout=10,
            env={"PATH": str(stubs), "HOME": str(home), "KUREK_DIR": str(kurek_dir),
                 "XDG_RUNTIME_DIR": str(runtime)},
        )
        return done, args_log.read_text().splitlines()

    return run


def test_refused_connection_is_reported(prompt):
    done, args = prompt(7)
    assert done.returncode == 7
    assert "connection refused" in done.stderr
    assert "--fail" in args and "--max-time" in args


def test_other_curl_failures_give_the_exit_code(prompt):
    done, _args = prompt(22)
    assert done.returncode == 22
    assert "curl exit 22" in done.stderr


def test_a_sent_prompt_prints_no_error(prompt):
    done, _args = prompt(0)
    assert done.returncode == 0 and done.stderr == ""


def test_a_timeout_says_the_prompt_may_still_run(prompt):
    done, _args = prompt(28)
    assert done.returncode == 28
    assert done.stderr == "kurek: no answer within 5 s; the prompt may still run\n"
