"""stream_deepseek: response closing, one retry, truncated-stream handling (offline)."""
from __future__ import annotations

import json

import core.llm_client as llm_client
from core.llm_client import stream_deepseek


class _Resp:
    def __init__(self, status_code=200, lines=None, text=""):
        self.status_code = status_code
        self.text = text
        self._lines = lines or []
        self.closed = False

    def iter_lines(self):
        yield from self._lines

    def close(self):
        self.closed = True


def _tok(text: str) -> bytes:
    return b"data: " + json.dumps({"choices": [{"delta": {"content": text}}]}).encode()


def _setup(monkeypatch, responses):
    monkeypatch.setattr("memory.config_manager.get_deepseek_key", lambda: "sk-test-not-real")
    monkeypatch.setattr(llm_client.time, "sleep", lambda _s: None)
    queue = list(responses)
    calls = []

    def post(*_a, **_k):
        calls.append(1)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(llm_client._HTTP, "post", post)
    return calls


def test_closes_response_on_early_generator_close(monkeypatch) -> None:
    resp = _Resp(lines=[_tok("Hello. "), _tok("World. "), b"data: [DONE]"])
    _setup(monkeypatch, [resp])
    gen = stream_deepseek(messages=[{"role": "user", "content": "hi"}])
    first = next(gen)
    assert first == {"type": "token", "token": "Hello. "}
    assert not resp.closed
    gen.close()
    assert resp.closed


def test_closes_response_on_normal_completion(monkeypatch) -> None:
    resp = _Resp(lines=[_tok("Hi there."), b"data: [DONE]"])
    _setup(monkeypatch, [resp])
    events = list(stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    assert events[-1]["type"] == "done"
    assert events[-1]["full_content"] == "Hi there."
    assert resp.closed


def test_retries_once_on_503(monkeypatch) -> None:
    bad = _Resp(status_code=503, text="overloaded")
    good = _Resp(lines=[_tok("Recovered."), b"data: [DONE]"])
    calls = _setup(monkeypatch, [bad, good])
    events = list(stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    assert len(calls) == 2
    assert bad.closed and good.closed
    assert events[-1] == {"type": "done", "full_content": "Recovered.", "reasoning_content": None}


def test_retry_only_once(monkeypatch) -> None:
    calls = _setup(monkeypatch, [_Resp(status_code=503), _Resp(status_code=502, text="bad gw")])
    events = list(stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    assert len(calls) == 2
    assert events == [{"type": "error", "error": "HTTP 502: bad gw"}]


def test_no_retry_on_401(monkeypatch) -> None:
    calls = _setup(monkeypatch, [_Resp(status_code=401, text="Unauthorized")])
    events = list(stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    assert len(calls) == 1
    assert events[0]["type"] == "error" and "401" in events[0]["error"]


def test_retries_connection_error(monkeypatch) -> None:
    good = _Resp(lines=[_tok("Ok then."), b"data: [DONE]"])
    calls = _setup(monkeypatch, [ConnectionError("reset"), good])
    events = list(stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    assert len(calls) == 2
    assert events[-1]["type"] == "done"


def test_truncated_stream_with_content_yields_done(monkeypatch) -> None:
    _setup(monkeypatch, [_Resp(lines=[_tok("Partial answer.")])])
    events = list(stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    assert events[-1]["type"] == "done"
    assert events[-1]["full_content"] == "Partial answer."


def test_truncated_stream_without_content_yields_error(monkeypatch) -> None:
    _setup(monkeypatch, [_Resp(lines=[])])
    events = list(stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    assert events == [{"type": "error", "error": "DeepSeek stream ended unexpectedly"}]


def test_finish_reason_counts_as_complete(monkeypatch) -> None:
    line = b"data: " + json.dumps(
        {"choices": [{"delta": {}, "finish_reason": "stop"}]}
    ).encode()
    _setup(monkeypatch, [_Resp(lines=[_tok("Done here."), line])])
    events = list(stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    assert events[-1]["type"] == "done"


def test_tool_calls_keep_reasoning_content(monkeypatch) -> None:
    chunk = {
        "choices": [{
            "delta": {
                "reasoning_content": "think",
                "tool_calls": [{"index": 0, "id": "c1", "function": {"name": "weather", "arguments": "{}"}}],
            },
            "finish_reason": "tool_calls",
        }]
    }
    _setup(monkeypatch, [_Resp(lines=[b"data: " + json.dumps(chunk).encode(), b"data: [DONE]"])])
    events = list(stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    assert events[-1]["type"] == "tool_calls"
    assert events[-1]["reasoning_content"] == "think"
    assert events[-1]["tool_calls"][0]["function"]["name"] == "weather"


def test_query_deepseek_default_model(monkeypatch) -> None:
    monkeypatch.setattr("memory.config_manager.get_deepseek_key", lambda: "sk-test-not-real")
    seen = {}

    class R:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "hi"}}]}

    def post(*_a, json=None, **_k):
        seen.update(json)
        return R()

    monkeypatch.setattr(llm_client._HTTP, "post", post)
    assert llm_client.query_deepseek(prompt="x", model="") == "hi"
    assert seen["model"] == "deepseek-flash"
