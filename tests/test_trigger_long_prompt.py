"""C12: a long typed prompt reaches the daemon whole, or kurek-trigger / kurek prompt refuse it loudly.

Safety: the trigger is built into a pytest temp dir and run with an env that REPLACES the whole
environment: XDG_RUNTIME_DIR, HOME and XDG_CONFIG_HOME are empty temp dirs (no install_path, so
no launcher), PATH holds only a systemctl stub that records it was called. A fake one-connection
server listens on the temp socket before the run, so the trigger never tries to start a daemon.
Only the action "prompt" is ever passed.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from tests.test_kurek_prompt_cli import _STUBS, _stub
from tests.test_trigger_json_escape import _compile_trigger

CLI = Path(__file__).resolve().parents[1] / "bin" / "kurek"
BASH = shutil.which("bash")


@pytest.fixture(scope="module")
def trigger_bin(tmp_path_factory) -> Path:
    return _compile_trigger(tmp_path_factory.mktemp("trigger-long") / "kurek-trigger")


def _sandbox(tmp_path: Path) -> dict:
    run, home, config, stubs = (tmp_path / d for d in ("run", "home", "config", "stubs"))
    for d in (run, home, config, stubs):
        d.mkdir()
    run.chmod(0o700)
    _stub(stubs / "systemctl", f'touch "{tmp_path / "spawned"}"\nexit 1')
    return {"XDG_RUNTIME_DIR": str(run), "HOME": str(home), "XDG_CONFIG_HOME": str(config), "PATH": str(stubs)}


def _serve_one(sock_path: Path, received: list[bytes]) -> threading.Thread:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    server.settimeout(5.0)

    def run() -> None:
        try:
            conn, _ = server.accept()
            with conn:
                buf = b""
                while b"\n" not in buf:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                received.append(buf)
                conn.sendall(b'{"status":"processing"}\n')
        finally:
            server.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _send(trigger_bin: Path, tmp_path: Path, *words: str) -> dict:
    env = _sandbox(tmp_path)
    sock_path = Path(env["XDG_RUNTIME_DIR"]) / "kurek.sock"
    assert str(sock_path).startswith(str(tmp_path))
    received: list[bytes] = []
    server = _serve_one(sock_path, received)
    done = subprocess.run([str(trigger_bin), "prompt", *words], env=env, capture_output=True, timeout=10)
    server.join(timeout=6)
    assert not server.is_alive()
    assert not (tmp_path / "spawned").exists()
    assert done.returncode == 0, done.stderr
    assert len(received) == 1
    return json.loads(received[0])


def test_a_10kb_prompt_with_quotes_arrives_whole(trigger_bin, tmp_path) -> None:
    p = ("word " * 2000)[:10000] + 'q"\\\n\tä'
    assert _send(trigger_bin, tmp_path, p) == {"action": "prompt", "prompt": p}


def test_long_arguments_are_joined_whole(trigger_bin, tmp_path) -> None:
    got = _send(trigger_bin, tmp_path, "x" * 6000, "y" * 6000)
    assert got == {"action": "prompt", "prompt": "x" * 6000 + " " + "y" * 6000}


def test_a_prompt_over_64kb_is_refused_before_connecting(trigger_bin, tmp_path) -> None:
    env = _sandbox(tmp_path)
    start = time.monotonic()
    done = subprocess.run([str(trigger_bin), "prompt", "z" * 70_000], env=env, capture_output=True,
                          text=True, timeout=2)
    assert time.monotonic() - start < 2
    assert done.returncode == 5
    assert "over the 65536-byte limit" in done.stderr
    assert not (tmp_path / "spawned").exists()


def test_kurek_prompt_reports_the_limit_and_does_not_fall_back_to_http(tmp_path) -> None:
    stubs, home, kurek_dir, runtime, cli_dir = (
        tmp_path / d for d in ("stubs", "home", "kurek_dir", "runtime", "cli")
    )
    for d in (stubs, home, kurek_dir, runtime, cli_dir):
        d.mkdir()
    for name, body in _STUBS.items():
        _stub(stubs / name, body)
    _stub(stubs / "curl", f'touch "{tmp_path / "curl-called"}"\nexit 0')
    (home / ".local" / "bin").mkdir(parents=True)
    _stub(home / ".local" / "bin" / "kurek-trigger", "exit 5")
    cli = cli_dir / "kurek"
    shutil.copy(CLI, cli)
    done = subprocess.run(
        [BASH, str(cli), "prompt", "hi"], capture_output=True, text=True, timeout=10,
        env={"PATH": str(stubs), "HOME": str(home), "KUREK_DIR": str(kurek_dir),
             "XDG_RUNTIME_DIR": str(runtime)},
    )
    assert done.returncode == 5
    assert done.stderr == "kurek: prompt not sent: it is over the 64 KB limit\n"
    assert not (tmp_path / "curl-called").exists()
