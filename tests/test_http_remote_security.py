"""Offline HTTP remote-access hardening tests (no API keys)."""
from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import kurek_daemon as daemon

ROOT = Path(__file__).resolve().parents[1]
STRONG_TOKEN = "a" * 32


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def http_server_factory(request):
    servers: list[Any] = []

    def _start(engine, *, force_remote: bool = False) -> int:
        port = _free_port()
        daemon.KurekHTTPHandler.engine = engine
        daemon.KurekHTTPHandler.force_remote_client = force_remote
        server = daemon.ThreadingHTTPServer(("127.0.0.1", port), daemon.KurekHTTPHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)

        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            server.shutdown()
            raise AssertionError("test HTTP server failed to start")
        return port

    def _stop():
        daemon.KurekHTTPHandler.force_remote_client = None
        for server in servers:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass

    request.addfinalizer(_stop)
    return _start


def _make_engine(**overrides):
    state = {"value": daemon.KurekState.IDLE}
    engine = SimpleNamespace(
        state=daemon.KurekState.IDLE,
        toggle=MagicMock(),
        handle_text_query=MagicMock(),
        actions=SimpleNamespace(run=MagicMock(return_value="ok")),
    )

    def _toggle():
        engine.state = daemon.KurekState.LISTENING
        state["value"] = engine.state

    engine.toggle.side_effect = _toggle

    def _handle(user_prompt, callback=None, mute_speaker=False, remote=False):
        if callback:
            callback({"type": "sentence", "sentence": "Hello from Kurek."})
            callback({"type": "done", "reply": "Hello from Kurek."})

    engine.handle_text_query.side_effect = _handle
    for k, v in overrides.items():
        setattr(engine, k, v)
    return engine


def test_bind_defaults_to_loopback(monkeypatch) -> None:
    monkeypatch.delenv("KUREK_BIND", raising=False)
    monkeypatch.delenv("KUREK_REMOTE_BIND", raising=False)
    monkeypatch.delenv("KUREK_REMOTE_TOKEN", raising=False)
    hosts, watch = daemon.resolve_http_bind_hosts()
    assert hosts == ["127.0.0.1"]
    assert watch is False


def test_remote_bind_refused_without_strong_token(monkeypatch) -> None:
    monkeypatch.delenv("KUREK_BIND", raising=False)
    monkeypatch.setenv("KUREK_REMOTE_BIND", "tailscale")
    monkeypatch.delenv("KUREK_REMOTE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="Remote HTTP bind refused"):
        daemon.resolve_http_bind_hosts()

    monkeypatch.setenv("KUREK_REMOTE_TOKEN", "too-short")
    with pytest.raises(RuntimeError, match="Remote HTTP bind refused"):
        daemon.resolve_http_bind_hosts()


def test_remote_bind_tailscale_with_strong_token(monkeypatch) -> None:
    monkeypatch.delenv("KUREK_BIND", raising=False)
    monkeypatch.setenv("KUREK_REMOTE_BIND", "tailscale")
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)
    monkeypatch.setattr(daemon, "detect_tailscale_ipv4", lambda: "100.64.1.2")
    hosts, watch = daemon.resolve_http_bind_hosts()
    assert hosts == ["127.0.0.1", "100.64.1.2"]
    assert watch is True


def test_explicit_wildcard_bind_warns_and_requires_token(monkeypatch, capsys) -> None:
    monkeypatch.setenv("KUREK_BIND", "0.0.0.0")
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)
    hosts, watch = daemon.resolve_http_bind_hosts()
    assert hosts == ["0.0.0.0"]
    assert watch is False
    err = capsys.readouterr().out
    assert "SECURITY WARNING" in err


def test_no_cors_star_header(monkeypatch, http_server_factory) -> None:
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)
    engine = _make_engine()
    port = http_server_factory(engine, force_remote=False)
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    conn.request("GET", "/status")
    resp = conn.getresponse()
    body = resp.read()
    headers = {k.lower(): v for k, v in resp.getheaders()}
    assert resp.status == 200
    assert "access-control-allow-origin" not in headers
    assert json.loads(body.decode())["state"] == "idle"
    conn.close()


def test_remote_status_ok_without_token(monkeypatch, http_server_factory) -> None:
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)
    engine = _make_engine()
    port = http_server_factory(engine, force_remote=True)
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    # Exact APK GET /status shape (no Authorization) — must stay 200 for online pill.
    conn.request("GET", "/status")
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode())
    assert resp.status == 200
    assert payload == {"state": "idle"}
    conn.close()


def test_remote_post_requires_token(monkeypatch, http_server_factory) -> None:
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)
    engine = _make_engine()
    port = http_server_factory(engine, force_remote=True)

    for path, body in (
        ("/toggle", b"{}"),
        ("/prompt", b'{"prompt":"hi"}'),
        ("/remote/prompt", b'{"prompt":"hi","remote":true}'),
    ):
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 401, path
        conn.close()

        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request(
            "POST",
            path,
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Authorization": "Bearer wrong-token-wrong-token-wrong!!",
            },
        )
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 401, path
        conn.close()


def test_remote_post_ok_with_correct_token(monkeypatch, http_server_factory) -> None:
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)
    engine = _make_engine()
    port = http_server_factory(engine, force_remote=True)
    body = b"{}"
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    conn.request(
        "POST",
        "/toggle",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Authorization": f"Bearer {STRONG_TOKEN}",
        },
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode())
    assert resp.status == 200
    assert payload["ok"] is True
    conn.close()


def test_apk_compat_remote_prompt_sse(monkeypatch, http_server_factory) -> None:
    """Replay KurekClient.java exact request headers/body against a remote peer."""
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)
    engine = _make_engine()
    port = http_server_factory(engine, force_remote=True)

    # Mirror KurekClient.sendPrompt payload construction.
    payload_obj = {"prompt": "ping from phone", "remote": True}
    body = json.dumps(payload_obj).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {STRONG_TOKEN}",
        "Content-Length": str(len(body)),
    }
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/remote/prompt", body=body, headers=headers)
    resp = conn.getresponse()
    assert resp.status == 200
    assert resp.getheader("Content-Type", "").startswith("text/event-stream")
    # Mirror KurekClient: read SSE lines until a "done" event (keep-alive won't EOF).
    lines: list[str] = []
    saw_done = False
    while True:
        line = resp.fp.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").strip()
        lines.append(text)
        if text.startswith("data:"):
            event = json.loads(text[5:].strip())
            if event.get("type") == "done":
                saw_done = True
                break
    conn.close()

    joined = "\n".join(lines)
    assert any(l.startswith("data:") for l in lines)
    assert '"type": "sentence"' in joined or '"type":"sentence"' in joined
    assert saw_done
    engine.handle_text_query.assert_called()
    kwargs = engine.handle_text_query.call_args.kwargs
    assert kwargs.get("remote") is True
    assert kwargs.get("mute_speaker") is True


def test_loopback_prompt_works_without_token(monkeypatch, http_server_factory) -> None:
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)
    engine = _make_engine()
    port = http_server_factory(engine, force_remote=False)
    body = b'{"prompt":"local hi"}'
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    conn.request(
        "POST",
        "/prompt",
        body=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    resp = conn.getresponse()
    payload = json.loads(resp.read().decode())
    assert resp.status == 200
    assert payload["status"] == "processing"
    conn.close()
    kwargs = engine.handle_text_query.call_args.kwargs
    assert kwargs.get("remote") is False


def test_loopback_prompt_rejected_with_origin(monkeypatch, http_server_factory) -> None:
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)
    engine = _make_engine()
    port = http_server_factory(engine, force_remote=False)
    body = b'{"prompt":"csrf"}'
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    conn.request(
        "POST",
        "/prompt",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Origin": "https://evil.example",
        },
    )
    resp = conn.getresponse()
    resp.read()
    assert resp.status == 403
    conn.close()
    engine.handle_text_query.assert_not_called()


def test_oversized_body_413(monkeypatch, http_server_factory) -> None:
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)
    engine = _make_engine()
    port = http_server_factory(engine, force_remote=False)
    huge = b"x" * (daemon.HTTP_MAX_BODY_BYTES + 1)
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    conn.request(
        "POST",
        "/prompt",
        body=huge,
        headers={"Content-Type": "application/json", "Content-Length": str(len(huge))},
    )
    resp = conn.getresponse()
    resp.read()
    assert resp.status == 413
    conn.close()


def test_bad_json_400(monkeypatch, http_server_factory) -> None:
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)
    engine = _make_engine()
    port = http_server_factory(engine, force_remote=False)
    body = b"{not-json"
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    conn.request(
        "POST",
        "/prompt",
        body=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    resp = conn.getresponse()
    resp.read()
    assert resp.status == 400
    conn.close()


def test_remote_blocks_exec_tools(monkeypatch) -> None:
    monkeypatch.delenv("KUREK_REMOTE_ALLOW_EXEC", raising=False)
    engine = daemon.KurekEngine.__new__(daemon.KurekEngine)
    engine.actions = SimpleNamespace(run=MagicMock(return_value="executed"))

    blocked = engine._run_tool("desktop_control", {"action": "task", "task": "x"}, remote=True)
    assert "disabled for remote" in blocked
    engine.actions.run.assert_not_called()

    allowed = engine._run_tool("web_search", {"query": "hi"}, remote=True)
    assert allowed == "executed"
    engine.actions.run.assert_called_once_with("web_search", {"query": "hi"})

    monkeypatch.setenv("KUREK_REMOTE_ALLOW_EXEC", "1")
    engine.actions.run.reset_mock()
    assert engine._run_tool("desktop_control", {"action": "list"}, remote=True) == "executed"


def test_local_exec_tools_unchanged(monkeypatch) -> None:
    monkeypatch.delenv("KUREK_REMOTE_ALLOW_EXEC", raising=False)
    engine = daemon.KurekEngine.__new__(daemon.KurekEngine)
    engine.actions = SimpleNamespace(run=MagicMock(return_value="local-ok"))
    assert engine._run_tool("desktop_control", {"action": "list"}, remote=False) == "local-ok"
    engine.actions.run.assert_called_once()


def test_bearer_parse_strict() -> None:
    assert daemon.parse_bearer_token("Bearer abc") == "abc"
    assert daemon.parse_bearer_token("bearer abc") is None
    assert daemon.parse_bearer_token("Bearerabc") is None
    assert daemon.parse_bearer_token("Bearer  abc") is None
    assert daemon.parse_bearer_token("Bearer abc def") is None
    assert daemon.parse_bearer_token(None) is None


def test_uds_prompt_still_works(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "run"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))

    engine = SimpleNamespace(
        state=daemon.KurekState.IDLE,
        state_lock=threading.Lock(),
        uds_subscribers=[],
        uds_lock=threading.Lock(),
        tts=SimpleNamespace(stop=lambda: None, is_playing=False),
        toggle=MagicMock(),
        handle_text_query=MagicMock(),
        set_state=MagicMock(),
    )
    sock_path = runtime / "kurek.sock"
    thread = threading.Thread(target=daemon.run_uds_server, args=(engine,), daemon=True)
    thread.start()

    deadline = time.time() + 5.0
    while time.time() < deadline and not sock_path.exists():
        time.sleep(0.05)
    assert sock_path.exists()

    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(2.0)
    conn.connect(str(sock_path))
    conn.sendall(b'{"action":"prompt","prompt":"from uds"}\n')
    data = conn.recv(4096)
    conn.close()
    assert b"processing" in data
    engine.handle_text_query.assert_called()


def test_no_hardcoded_remote_secrets_in_tracked_files() -> None:
    """Extend installer hygiene: no live token prefix or owner Tailscale IP."""
    files = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        text=False,
    ).split(b"\0")
    # Construct needles so this test file does not contain the forbidden literals.
    needles = (b"kurek" + b"_sec_", b"100." + b"116.")
    hits: list[str] = []
    for raw in files:
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="replace")
        path = ROOT / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        for needle in needles:
            if needle in data:
                hits.append(f"{rel} contains {needle!r}")
    assert hits == [], "hardcoded remote secrets found:\n" + "\n".join(hits)
