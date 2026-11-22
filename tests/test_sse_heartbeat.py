"""During a phone turn the daemon writes an SSE comment every SSE_HEARTBEAT_S seconds, from one writer."""
import json
import socket
import threading
import time
from http.client import HTTPConnection

import pytest

import kurek_daemon as daemon
from tests.test_http_remote_security import STRONG_TOKEN, _make_engine, http_server_factory  # noqa: F401  (fixture)

KEEP_ALIVE = ": keep-alive"


@pytest.fixture
def phone(monkeypatch, http_server_factory):  # noqa: F811
    """start(side_effect) -> port of a remote-forced server whose engine runs side_effect(callback)."""
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)

    def start(side_effect):
        engine = _make_engine()
        engine.handle_text_query.side_effect = lambda _p, callback=None, **_kw: side_effect(callback)
        return http_server_factory(engine, force_remote=True)

    return start


def _request() -> tuple[bytes, dict]:
    body = json.dumps({"prompt": "slow one"}).encode()
    return body, {"Content-Type": "application/json", "Authorization": f"Bearer {STRONG_TOKEN}",
                  "Content-Length": str(len(body))}


def _read_lines(port: int, read_timeout: float = 5) -> list[str]:
    """Every non-empty line of the reply, read until the server closes (Connection: close)."""
    body, headers = _request()
    conn = HTTPConnection("127.0.0.1", port, timeout=read_timeout)
    try:
        conn.request("POST", "/remote/prompt", body=body, headers=headers)
        resp = conn.getresponse()
        assert resp.status == 200
        lines = []
        while line := resp.fp.readline():
            if text := line.decode().strip():
                lines.append(text)
        return lines
    finally:
        conn.close()


def _alive() -> list:
    deadline = time.monotonic() + 2
    while True:
        alive = [t for t in threading.enumerate() if t.name == "kurek-sse-heartbeat"]
        if not alive or time.monotonic() > deadline:
            return alive
        time.sleep(0.02)


def _what_the_app_sees(lines: list[str]) -> list[dict]:
    """KurekClient 0.2.3 keeps lines starting with "data:" and skips the rest; every line must be one or a comment."""
    events = []
    for line in lines:
        if line.startswith("data:"):
            events.append(json.loads(line[5:]))
        else:
            assert line.startswith(":"), line
    return events


def test_heartbeats_keep_a_slow_turn_alive_and_stop_at_done(monkeypatch, phone):
    monkeypatch.setattr(daemon, "SSE_HEARTBEAT_S", 0.05)
    monkeypatch.setattr(daemon.KurekHTTPHandler, "timeout", 0.3)

    def turn(cb):
        cb({"type": "tool", "name": "web_search", "description": "x", "result": "y"})
        time.sleep(0.6)  # longer than both the client's read timeout and the socket timeout
        cb({"type": "done", "reply": "ok"})
        time.sleep(0.1)  # a heartbeat here would land after done

    lines = _read_lines(phone(turn), read_timeout=0.3)
    assert json.loads(lines[0][5:])["type"] == "tool"
    assert lines.count(KEEP_ALIVE) >= 2
    assert json.loads(lines[-1][5:])["type"] == "done"
    assert [e["type"] for e in _what_the_app_sees(lines)] == ["tool", "done"]
    assert _alive() == []


def test_without_heartbeats_the_same_gap_trips_the_read_timeout(phone):
    returned = threading.Event()

    def turn(cb):
        cb({"type": "tool", "name": "web_search", "description": "x", "result": "y"})
        time.sleep(0.6)
        cb({"type": "done", "reply": "ok"})
        returned.set()

    port = phone(turn)  # default SSE_HEARTBEAT_S: no heartbeat within 0.6 s
    with pytest.raises(TimeoutError):
        _read_lines(port, read_timeout=0.3)
    assert returned.wait(5)
    assert _alive() == []


def test_a_quick_turn_gets_no_heartbeat(phone):
    def turn(cb):
        cb({"type": "sentence", "sentence": "Hi."})
        cb({"type": "done", "reply": "Hi."})

    lines = _read_lines(phone(turn))
    assert KEEP_ALIVE not in lines
    assert [e["type"] for e in _what_the_app_sees(lines)] == ["sentence", "done"]
    assert _alive() == []


def test_a_phone_that_hangs_up_ends_the_heartbeat(monkeypatch, phone):
    monkeypatch.setattr(daemon, "SSE_HEARTBEAT_S", 0.02)
    returned = threading.Event()

    def turn(cb):
        for _ in range(20):
            cb({"type": "sentence", "sentence": "s" * 1000})
            time.sleep(0.02)
        cb({"type": "done", "reply": "ok"})
        returned.set()

    port = phone(turn)
    body, headers = _request()
    head = "POST /remote/prompt HTTP/1.1\r\nHost: x\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(head.encode() + b"\r\n" + body)
        got = b""
        while b"\r\n\r\n" not in got:
            got += s.recv(1)
    assert returned.wait(5)
    assert _alive() == []


def test_a_phone_that_stops_reading_ends_the_turn(monkeypatch, phone):
    monkeypatch.setattr(daemon, "SSE_HEARTBEAT_S", 0.02)
    monkeypatch.setattr(daemon.KurekHTTPHandler, "timeout", 0.3)
    returned = threading.Event()

    def turn(cb):
        for _ in range(150):  # ~15 MB: more than the socket buffers hold while nobody reads
            cb({"type": "sentence", "sentence": "s" * 100_000})
        cb({"type": "done", "reply": "ok"})
        returned.set()

    port = phone(turn)
    body, headers = _request()
    head = "POST /remote/prompt HTTP/1.1\r\nHost: x\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        s.settimeout(5)
        s.connect(("127.0.0.1", port))
        s.sendall(head.encode() + b"\r\n" + body)
        assert returned.wait(5)  # the blocked write gave up after the socket timeout
        assert _alive() == []
    finally:
        s.close()


def test_events_and_heartbeats_never_interleave(monkeypatch, phone):
    monkeypatch.setattr(daemon, "SSE_HEARTBEAT_S", 0.001)

    def turn(cb):
        other = threading.Thread(target=lambda: [cb({"type": "sentence", "sentence": "a" * 8000})
                                                 for _ in range(200)])
        other.start()
        for _ in range(200):
            cb({"type": "sentence", "sentence": "b" * 8000})
        other.join()
        cb({"type": "done", "reply": "ok"})

    events = _what_the_app_sees(_read_lines(phone(turn)))
    sentences = [e["sentence"] for e in events if e["type"] == "sentence"]
    assert len(sentences) == 400
    assert sorted(set(sentences)) == ["a" * 8000, "b" * 8000]
    assert events[-1]["type"] == "done"
    assert _alive() == []


def test_an_error_only_turn_ends_the_stream(monkeypatch, phone):
    monkeypatch.setattr(daemon, "SSE_HEARTBEAT_S", 0.05)
    lines = _read_lines(phone(lambda cb: cb({"type": "error", "error": "x"})))
    assert [e["type"] for e in _what_the_app_sees(lines)] == ["error"]
    assert _alive() == []


def test_a_large_event_on_a_slow_link_is_not_cut(monkeypatch, phone):
    """The socket timeout bounds a period without progress, not a whole multi-MB event."""
    monkeypatch.setattr(daemon.KurekHTTPHandler, "timeout", 0.5)
    setup = daemon.KurekHTTPHandler.setup

    def small_send_buffer(handler):  # so the event cannot sit in kernel buffers all at once
        setup(handler)
        handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16384)

    monkeypatch.setattr(daemon.KurekHTTPHandler, "setup", small_send_buffer)
    audio = "A" * 1_500_000

    def turn(cb):
        cb({"type": "sentence", "sentence": "Long.", "audio": audio})
        cb({"type": "done", "reply": "Long."})

    port = phone(turn)
    body, headers = _request()
    head = "POST /remote/prompt HTTP/1.1\r\nHost: x\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        s.settimeout(5)
        s.connect(("127.0.0.1", port))
        s.sendall(head.encode() + b"\r\n" + body)
        start, data = time.monotonic(), bytearray()
        while chunk := s.recv(8192):  # steady but slow: at most 8 KB per 5 ms, ~1.6 MB/s
            data += chunk
            time.sleep(0.005)
        elapsed = time.monotonic() - start
    finally:
        s.close()
    lines = [ln for ln in data.split(b"\r\n\r\n", 1)[1].decode().splitlines() if ln.strip()]
    events = _what_the_app_sees(lines)
    assert [e["type"] for e in events] == ["sentence", "done"]
    assert events[0]["audio"] == audio
    assert elapsed > 0.5  # the event took longer to drain than the socket timeout
    assert _alive() == []
