"""A3: a dead phone does not hold up the PC. A local prompt that barges in shuts the phone turn's stream
down, so a write blocked on the phone returns at once; a phone prompt never does that to another stream.
Real HTTP handler on a throwaway port, real engine with fakes: no mic, mpv or network."""
from __future__ import annotations

import socket
import threading
import time

import pytest

import kurek_daemon as daemon
from tests.test_http_remote_security import STRONG_TOKEN, http_server_factory  # noqa: F401  (fixture)
from tests.test_sse_heartbeat import _read_lines, _request, _what_the_app_sees
from tests.test_stream_errors import _make_lite_engine


@pytest.fixture
def server(monkeypatch, http_server_factory):  # noqa: F811
    """(engine, port, log): a phone turn sends a large event then "done"; a local turn logs that it ran."""
    monkeypatch.setenv("KUREK_REMOTE_TOKEN", STRONG_TOKEN)
    monkeypatch.setattr(daemon.KurekHTTPHandler, "timeout", 2.5)  # a stalled write would wait 10 s
    setup = daemon.KurekHTTPHandler.setup

    def small_send_buffer(handler):
        setup(handler)
        handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16384)

    monkeypatch.setattr(daemon.KurekHTTPHandler, "setup", small_send_buffer)
    engine = _make_lite_engine(monkeypatch)
    log: dict = {}

    def inner(user_prompt, callback=None, **_kw):
        if callback is None:
            log["local_turn"] = time.monotonic()
            return "ok"
        log["phone_writing"] = time.monotonic()
        callback({"type": "sentence", "sentence": "x" * 4_000_000})  # blocks while the phone takes nothing
        log["phone_write_returned"] = time.monotonic()
        callback({"type": "done", "reply": "ok"})
        return "ok"

    engine._handle_text_query_inner = inner
    return engine, http_server_factory(engine, force_remote=True), log


def _frozen_phone(port: int) -> socket.socket:
    body, headers = _request()
    head = "POST /remote/prompt HTTP/1.1\r\nHost: x\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    s.connect(("127.0.0.1", port))
    s.sendall(head.encode() + b"\r\n" + body)
    return s  # never read: the server's write blocks


def _wait_for(cond, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def test_a_local_prompt_ends_a_write_blocked_on_a_dead_phone(server) -> None:
    engine, port, log = server
    phone = _frozen_phone(port)
    try:
        assert _wait_for(lambda: "phone_writing" in log)
        time.sleep(0.3)
        assert "phone_write_returned" not in log  # the write is blocked on the phone
        barged = time.monotonic()
        local = threading.Thread(target=engine.handle_text_query, args=("what time is it",), daemon=True)
        local.start()
        assert _wait_for(lambda: "local_turn" in log, 2.0)
        assert log["phone_write_returned"] - barged < 0.5
        assert log["local_turn"] - barged < 1.0
        local.join(2)
    finally:
        phone.close()
    assert _wait_for(lambda: not daemon._PHONE_STREAMS)


def test_a_phone_prompt_never_shuts_another_phone_stream(server) -> None:
    engine, port, log = server
    phone = _frozen_phone(port)
    try:
        assert _wait_for(lambda: "phone_writing" in log)
        events: list[dict] = []
        second = threading.Thread(target=engine.handle_text_query, args=("from the phone",),
                                  kwargs={"callback": events.append, "mute_speaker": True}, daemon=True)
        second.start()
        time.sleep(0.5)
        assert "phone_write_returned" not in log  # still blocked: nothing shut the first stream down
    finally:
        phone.close()
    assert _wait_for(lambda: "phone_write_returned" in log)
    second.join(3)


def test_a_normal_phone_turn_is_untouched(server) -> None:
    _engine, port, log = server
    events = _what_the_app_sees(_read_lines(port))
    assert [e["type"] for e in events] == ["sentence", "done"]
    assert len(events[0]["sentence"]) == 4_000_000
    assert _wait_for(lambda: not daemon._PHONE_STREAMS)
