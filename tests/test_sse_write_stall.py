"""C6: a phone that is briefly frozen gets SSE_WRITE_STALL_FACTOR x the read timeout to take the next
bytes; reading a request is still bounded by the plain 30 s timeout."""
import socket
import time

import kurek_daemon as daemon
from tests.test_http_remote_security import http_server_factory  # noqa: F401  (fixture)
from tests.test_sse_heartbeat import _alive, _request, _what_the_app_sees, phone  # noqa: F401  (fixture)


def test_write_stall_factor_and_read_timeout() -> None:
    assert daemon.SSE_WRITE_STALL_FACTOR == 4
    assert daemon.KurekHTTPHandler.timeout == 30


def test_a_paused_phone_still_gets_the_whole_reply(monkeypatch, phone) -> None:  # noqa: F811
    monkeypatch.setattr(daemon.KurekHTTPHandler, "timeout", 0.25)  # writes may stall 1.0 s
    setup = daemon.KurekHTTPHandler.setup

    def small_send_buffer(handler):  # so the reply cannot sit in kernel buffers while the phone pauses
        setup(handler)
        handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16384)

    monkeypatch.setattr(daemon.KurekHTTPHandler, "setup", small_send_buffer)

    def turn(cb):
        for _ in range(30):
            cb({"type": "sentence", "sentence": "s" * 100_000})
        cb({"type": "done", "reply": "ok"})

    port = phone(turn)
    body, headers = _request()
    head = "POST /remote/prompt HTTP/1.1\r\nHost: x\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        s.settimeout(5)
        s.connect(("127.0.0.1", port))
        s.sendall(head.encode() + b"\r\n" + body)
        time.sleep(0.5)  # the phone is frozen longer than the read timeout
        data = bytearray()
        while chunk := s.recv(65536):
            data += chunk
    finally:
        s.close()
    lines = [ln for ln in data.split(b"\r\n\r\n", 1)[1].decode().splitlines() if ln.strip()]
    events = _what_the_app_sees(lines)
    assert [e["type"] for e in events] == ["sentence"] * 30 + ["done"]
    assert _alive() == []
