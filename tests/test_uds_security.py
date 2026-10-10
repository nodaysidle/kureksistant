"""UDS path security: 0600 mode, no /tmp fallback, peer-uid rejection."""
from __future__ import annotations

import inspect
import os
import socket
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_no_tmp_fallback_in_path_resolver() -> None:
    import kurek_daemon as daemon

    source = inspect.getsource(daemon._get_uds_socket_path)
    assert "/tmp/kurek.sock" not in source
    assert "/tmp/" not in source


def test_private_runtime_when_xdg_unset(monkeypatch, tmp_path: Path) -> None:
    import kurek_daemon as daemon

    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    sock = daemon._get_uds_socket_path()
    assert sock == tmp_path / ".cache" / "kurek" / "run" / "kurek.sock"
    assert sock.parent.is_dir()
    mode = stat.S_IMODE(sock.parent.stat().st_mode)
    assert mode == 0o700


def test_socket_mode_is_0600(monkeypatch, tmp_path: Path) -> None:
    import kurek_daemon as daemon

    runtime = tmp_path / "run"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))

    engine = SimpleNamespace(
        state=daemon.KurekState.IDLE,
        state_lock=threading.Lock(),
        uds_subscribers=[],
        uds_lock=threading.Lock(),
        tts=SimpleNamespace(stop=lambda: None, is_playing=False),
    )

    sock_path = runtime / "kurek.sock"
    thread = threading.Thread(target=daemon.run_uds_server, args=(engine,), daemon=True)
    thread.start()

    deadline = time.time() + 5.0
    while time.time() < deadline and not sock_path.exists():
        time.sleep(0.05)
    assert sock_path.exists()

    mode = stat.S_IMODE(sock_path.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_peer_uid_mismatch_rejected(monkeypatch) -> None:
    import kurek_daemon as daemon

    fake = MagicMock(spec=socket.socket)
    monkeypatch.setattr(daemon, "_peer_uid", lambda _c: os.getuid() + 1)
    assert daemon._uds_peer_allowed(fake) is False

    monkeypatch.setattr(daemon, "_peer_uid", lambda _c: os.getuid())
    assert daemon._uds_peer_allowed(fake) is True

    monkeypatch.setattr(daemon, "_peer_uid", lambda _c: None)
    assert daemon._uds_peer_allowed(fake) is False


def test_run_uds_server_rejects_mismatched_peer(monkeypatch, tmp_path: Path) -> None:
    import kurek_daemon as daemon

    runtime = tmp_path / "run"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(daemon, "_uds_peer_allowed", lambda _c: False)

    engine = SimpleNamespace(
        state=daemon.KurekState.IDLE,
        state_lock=threading.Lock(),
        uds_subscribers=[],
        uds_lock=threading.Lock(),
        tts=SimpleNamespace(stop=lambda: None, is_playing=False),
    )
    sock_path = runtime / "kurek.sock"
    threading.Thread(target=daemon.run_uds_server, args=(engine,), daemon=True).start()

    deadline = time.time() + 5.0
    while time.time() < deadline and not sock_path.exists():
        time.sleep(0.05)
    assert sock_path.exists()

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2.0)
    client.connect(str(sock_path))
    client.sendall(b'{"action":"status"}\n')
    data = client.recv(256)
    client.close()
    assert b"unauthorized" in data
