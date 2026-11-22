"""bin/kurek-ask hands each typed line to `kurek prompt` as one untouched argument."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ASK = Path(__file__).resolve().parents[1] / "bin" / "kurek-ask"


@pytest.fixture
def fake_kurek(tmp_path: Path):
    log = tmp_path / "calls.txt"
    exe = tmp_path / "kurek"
    exe.write_text(f'#!/bin/sh\nprintf \'%s|%s|%s\\n\' "$#" "$1" "$2" >> "{log}"\n')
    exe.chmod(0o755)

    def run(typed: str) -> list[str]:
        subprocess.run(["bash", str(ASK)], input=typed, text=True, check=True,
                       env={"PATH": "/usr/bin:/bin", "KUREK_BIN": str(exe)}, timeout=10)
        return log.read_text().splitlines() if log.exists() else []

    return run


def test_a_line_is_sent_as_one_argument(fake_kurek):
    assert fake_kurek("delete GateTest.md\n\n") == ["2|prompt|delete GateTest.md"]


def test_quotes_and_symbols_arrive_unchanged(fake_kurek):
    line = "delete \"Gate Test.md\" it's $HOME `x` \\n *"
    assert fake_kurek(f"{line}\n\n") == [f"2|prompt|{line}"]


def test_several_lines_then_an_empty_one_closes(fake_kurek):
    assert fake_kurek("delete x\nyes\n\nnever sent\n") == ["2|prompt|delete x", "2|prompt|yes"]


@pytest.mark.parametrize("typed", ["", "\n", "   \n", "\t\n", "   \nhello\n"])
def test_nothing_to_send_makes_no_call(fake_kurek, typed):
    assert fake_kurek(typed) == []


def _fake_exiting(tmp_path: Path, code: int) -> Path:
    exe = tmp_path / "kurek"
    exe.write_text(f'#!/bin/sh\necho "stdout noise"\necho "stderr noise" >&2\nexit {code}\n')
    exe.chmod(0o755)
    return exe


def _ask(exe: Path, typed: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(ASK)], input=typed, text=True, capture_output=True,
                          env={"PATH": "/usr/bin:/bin", "KUREK_BIN": str(exe)}, timeout=10)


def test_a_failed_send_prints_one_line_and_the_box_stays_open(tmp_path: Path):
    done = _ask(_fake_exiting(tmp_path, 7), "hello\nagain\n\n")
    assert done.returncode == 0
    assert done.stdout == ""
    assert done.stderr.splitlines() == [
        "kurek-ask: not sent (kurek prompt exit 7). Is the daemon running? Try: kurek doctor"
    ] * 2


def test_a_successful_send_prints_nothing(tmp_path: Path):
    done = _ask(_fake_exiting(tmp_path, 0), "hello\n\n")
    assert done.stdout == "" and done.stderr == ""
