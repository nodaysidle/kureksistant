"""Compile kurek-trigger and verify JSON escaping round-trips via a mock UDS server."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TRIGGER_SRC = ROOT / "bin" / "kurek-trigger.c"


def _compile_trigger(out: Path) -> Path:
    cmd = [
        "gcc",
        "-O3",
        "-Wall",
        "-Wextra",
        "-Werror",
        str(TRIGGER_SRC),
        "-o",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"gcc failed:\n{proc.stderr}\n{proc.stdout}"
    return out


@pytest.fixture(scope="module")
def trigger_bin(tmp_path_factory) -> Path:
    out_dir = tmp_path_factory.mktemp("trigger-build")
    return _compile_trigger(out_dir / "kurek-trigger")


def _serve_one_line(sock_path: Path, received: list[str], ready: threading.Event) -> None:
    if sock_path.exists():
        sock_path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    os.chmod(sock_path, 0o600)
    server.listen(1)
    ready.set()
    server.settimeout(5.0)
    conn, _ = server.accept()
    with conn:
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        received.append(buf.decode("utf-8"))
        conn.sendall(b'{"ok":true}\n')
    server.close()


def test_trigger_json_escape_roundtrip(trigger_bin: Path, tmp_path: Path) -> None:
    runtime = tmp_path / "run"
    runtime.mkdir()
    sock_path = runtime / "kurek.sock"

    # Prompt with quotes, backslashes, and newlines.
    prompt = 'say "hi"\\there\nnext line\x01ctrl'
    received: list[str] = []
    ready = threading.Event()
    t = threading.Thread(target=_serve_one_line, args=(sock_path, received, ready), daemon=True)
    t.start()
    assert ready.wait(timeout=3.0)

    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = str(runtime)
    # Ensure we do not try to spawn a real daemon if connect races.
    env.pop("XDG_CONFIG_HOME", None)

    proc = subprocess.run(
        [str(trigger_bin), "prompt", prompt],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert proc.returncode == 0, f"trigger failed: rc={proc.returncode} stderr={proc.stderr!r}"
    t.join(timeout=3.0)
    assert received, "mock UDS server received no payload"

    line = received[0].strip()
    payload = json.loads(line)
    assert payload["action"] == "prompt"
    assert payload["prompt"] == prompt


def test_trigger_compiles_wall_wextra_werror(tmp_path: Path) -> None:
    _compile_trigger(tmp_path / "kurek-trigger")


def test_trigger_source_has_no_home_arch() -> None:
    text = TRIGGER_SRC.read_text(encoding="utf-8")
    assert "/home/arch" not in text
    assert "/tmp/kurek.sock" not in text
