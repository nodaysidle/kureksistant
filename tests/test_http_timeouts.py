"""An idle or half-sent HTTP request is dropped; a long SSE reply is not cut."""
import json
import socket
import time
from http.client import HTTPConnection

import kurek_daemon as daemon
from tests.test_http_remote_security import STRONG_TOKEN, _make_engine, http_server_factory  # noqa: F401  (fixture)


def _closed_without_reply(port: int, send: bytes) -> bool:
    """Send a partial request; True when the server closes the connection within 3 s, sending nothing."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        if send:
            s.sendall(send)
        start = time.monotonic()
        got = s.recv(1)
        return got == b"" and time.monotonic() - start < 3


def test_handler_timeout_is_30_seconds():
    assert daemon.KurekHTTPHandler.timeout == 30


def test_an_idle_connection_is_closed(monkeypatch, http_server_factory):  # noqa: F811
    monkeypatch.setattr(daemon.KurekHTTPHandler, "timeout", 0.3)
    port = http_server_factory(_make_engine())
    assert _closed_without_reply(port, b"")


def test_half_sent_headers_are_dropped(monkeypatch, http_server_factory):  # noqa: F811
    monkeypatch.setattr(daemon.KurekHTTPHandler, "timeout", 0.3)
    port = http_server_factory(_make_engine())
    assert _closed_without_reply(port, b"GET /status HTTP/1.1\r\nHost: x\r\n")


def test_half_sent_body_is_dropped(monkeypatch, http_server_factory):  # noqa: F811
    monkeypatch.setattr(daemon.KurekHTTPHandler, "timeout", 0.3)
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)
    engine = _make_engine()
    port = http_server_factory(engine, force_remote=True)
    head = (
        "POST /remote/prompt HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
        f"Authorization: Bearer {STRONG_TOKEN}\r\nContent-Length: 100\r\n\r\n"
    ).encode()
    assert _closed_without_reply(port, head + b'{"prompt":')
    engine.handle_text_query.assert_not_called()


def test_a_gap_longer_than_the_timeout_does_not_cut_the_stream(monkeypatch, http_server_factory):  # noqa: F811
    monkeypatch.setattr(daemon.KurekHTTPHandler, "timeout", 0.3)
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)

    def slow(user_prompt, callback=None, **_kw):
        callback({"type": "sentence", "sentence": "First."})
        time.sleep(0.8)
        callback({"type": "done", "reply": "First."})

    engine = _make_engine()
    engine.handle_text_query.side_effect = slow
    port = http_server_factory(engine, force_remote=True)
    body = json.dumps({"prompt": "slow one"}).encode()
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/remote/prompt", body=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {STRONG_TOKEN}",
        "Content-Length": str(len(body))})
    resp = conn.getresponse()
    assert resp.status == 200
    types = []
    while True:
        line = resp.fp.readline()
        if not line:
            break
        text = line.decode().strip()
        if text.startswith("data:"):
            types.append(json.loads(text[5:])["type"])
            if types[-1] == "done":
                break
    conn.close()
    assert types == ["sentence", "done"]
