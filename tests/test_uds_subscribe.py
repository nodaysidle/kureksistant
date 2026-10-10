"""UDS state subscribers must stay open and receive broadcasts."""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace


def _read_json_line(sock: socket.socket, timeout: float = 3.0) -> dict:
    sock.settimeout(timeout)
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise AssertionError("socket closed before newline")
        buf += chunk
    line, _rest = buf.split(b"\n", 1)
    return json.loads(line.decode("utf-8"))


def test_uds_subscribe_receives_state_broadcast(monkeypatch, tmp_path: Path) -> None:
    import kurek_daemon as daemon

    runtime = tmp_path / "run"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))

    # Lightweight engine that only exercises UDS state broadcast.
    engine = SimpleNamespace(
        state=daemon.KurekState.IDLE,
        state_lock=threading.Lock(),
        uds_subscribers=[],
        uds_lock=threading.Lock(),
        tts=SimpleNamespace(stop=lambda: None, is_playing=False),
    )

    def set_state(new_state: str):
        with engine.state_lock:
            engine.state = new_state
        daemon.KurekEngine._broadcast_state_uds(engine, new_state)

    engine.set_state = set_state
    engine.toggle = lambda: None
    engine.handle_text_query = lambda *_a, **_k: None

    # Avoid early-return if a stale socket path exists from another process.
    sock_path = runtime / "kurek.sock"
    if sock_path.exists():
        sock_path.unlink()

    server_thread = threading.Thread(
        target=daemon.run_uds_server,
        args=(engine,),
        daemon=True,
    )
    server_thread.start()

    deadline = time.time() + 5.0
    while time.time() < deadline and not sock_path.exists():
        time.sleep(0.05)
    assert sock_path.exists(), "UDS server did not create socket"

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5.0)
    client.connect(str(sock_path))
    client.sendall(b'{"action":"subscribe"}\n')

    initial = _read_json_line(client)
    assert initial.get("event") == "state"
    assert initial.get("state") == daemon.KurekState.IDLE

    # Subscriber must still be registered (the bug closed the socket in finally).
    deadline = time.time() + 2.0
    while time.time() < deadline and not engine.uds_subscribers:
        time.sleep(0.05)
    assert len(engine.uds_subscribers) == 1

    engine.set_state(daemon.KurekState.THINKING)
    event = _read_json_line(client)
    assert event == {"event": "state", "state": daemon.KurekState.THINKING}

    engine.set_state(daemon.KurekState.SPEAKING)
    event = _read_json_line(client)
    assert event == {"event": "state", "state": daemon.KurekState.SPEAKING}

    # Disconnect cleans up the subscriber list.
    client.close()
    deadline = time.time() + 2.0
    while time.time() < deadline and engine.uds_subscribers:
        time.sleep(0.05)
    assert engine.uds_subscribers == []


def test_broadcast_removes_dead_subscriber() -> None:
    import kurek_daemon as daemon

    dead_a, dead_b = socket.socketpair()
    dead_b.close()  # peer closed → sendall will fail

    engine = SimpleNamespace(
        uds_subscribers=[dead_a],
        uds_lock=threading.Lock(),
    )
    daemon.KurekEngine._broadcast_state_uds(engine, daemon.KurekState.LISTENING)
    assert engine.uds_subscribers == []
    try:
        dead_a.close()
    except Exception:
        pass
