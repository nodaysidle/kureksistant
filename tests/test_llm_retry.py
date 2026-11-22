"""stream_deepseek retry: a dropped pooled connection is retried at once, 429/5xx after the backoff (offline)."""
from __future__ import annotations

import http.client

import requests
from urllib3.exceptions import ProtocolError

import core.llm_client as llm_client
from core.llm_client import stream_deepseek
from tests.test_core_llm_stream import _Resp, _setup, _tok

_MSGS = [{"role": "user", "content": "hi"}]


def _dropped():
    return requests.ConnectionError(ProtocolError("Connection aborted.", http.client.RemoteDisconnected(
        "Remote end closed connection without response")))


def _good():
    return _Resp(lines=[_tok("Ok then."), b"data: [DONE]"])


def _run(monkeypatch, responses):
    calls = _setup(monkeypatch, responses)
    slept: list = []
    monkeypatch.setattr(llm_client.time, "sleep", slept.append)
    events = list(stream_deepseek(messages=_MSGS))
    return calls, slept, events


def test_dropped_connection_is_retried_at_once(monkeypatch) -> None:
    calls, slept, events = _run(monkeypatch, [_dropped(), _good()])
    assert events[-1]["type"] == "done"
    assert len(calls) == 2
    assert slept == []


def test_connection_reset_is_retried_at_once(monkeypatch) -> None:
    calls, slept, events = _run(monkeypatch, [ConnectionResetError(104, "Connection reset by peer"), _good()])
    assert events[-1]["type"] == "done"
    assert len(calls) == 2
    assert slept == []


def test_429_keeps_the_backoff(monkeypatch) -> None:
    calls, slept, _events = _run(monkeypatch, [_Resp(status_code=429, text="slow down"), _good()])
    assert len(calls) == 2
    assert slept == [llm_client._STREAM_RETRY_BACKOFF_S]


def test_connect_timeout_keeps_the_backoff(monkeypatch) -> None:
    _calls, slept, _events = _run(monkeypatch, [requests.ConnectTimeout("connect timed out"), _good()])
    assert slept == [llm_client._STREAM_RETRY_BACKOFF_S]


def test_dropped_twice_is_one_error(monkeypatch) -> None:
    calls, slept, events = _run(monkeypatch, [_dropped(), _dropped()])
    assert len(calls) == 2
    assert events[0]["type"] == "error"
    assert len(events) == 1
    assert slept == []


class _ResetAfterOutput(_Resp):
    def iter_lines(self):
        yield _tok("Partial answer.")
        raise ConnectionResetError(104, "reset")


def test_reset_after_output_is_not_retried(monkeypatch) -> None:
    calls, _slept, events = _run(monkeypatch, [_ResetAfterOutput()])
    assert len(calls) == 1
    assert events[-1] == {"type": "done", "full_content": "Partial answer.", "reasoning_content": None}


def test_dropped_connection_classifier() -> None:
    assert llm_client.dropped_connection(requests.ConnectionError("network down")) is False
    assert llm_client.dropped_connection(_dropped()) is True
