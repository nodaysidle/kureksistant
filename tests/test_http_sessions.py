"""STT uploads and DeepSeek web search reuse one HTTPS session per module (offline)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
from urllib3.exceptions import ProtocolError

from actions import web_search as ws
from core import stt
from core.llm_client import post_once_more_if_dropped
from core.stt import DeepgramSTT, OpenRouterSTT, STTError
from tests.test_core_stt import AUDIO, _Resp
from tests.test_latency_defaults import _no_bare_post
from tests.test_llm_retry import _dropped
from tests.test_web_search_deepseek import _resp


def _fake_stt_post(monkeypatch, *outcomes):
    """Patch the STT session: each call takes the next outcome (raised when it is an exception)."""
    urls: list = []
    queue = list(outcomes)

    def post(url, *_a, **_k):
        urls.append(url)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(requests, "post", _no_bare_post)
    monkeypatch.setattr(stt._HTTP, "post", post)
    return urls


def test_stt_engines_share_one_session(monkeypatch) -> None:
    both = _Resp(200, {"text": "hi", "results": {"channels": [{"alternatives": [{"transcript": "yo"}]}]}})
    urls = _fake_stt_post(monkeypatch, both)
    assert isinstance(stt._HTTP, requests.Session)
    assert OpenRouterSTT(api_key="k").transcribe(AUDIO) == "hi"
    assert OpenRouterSTT(api_key="k").transcribe(AUDIO) == "hi"
    assert DeepgramSTT(api_key="k").transcribe(AUDIO) == "yo"
    assert len(urls) == 3


def test_stt_resends_once_when_the_pooled_connection_was_closed(monkeypatch) -> None:
    urls = _fake_stt_post(monkeypatch, _dropped(), _Resp(200, {"text": "hi"}))
    assert OpenRouterSTT(api_key="k").transcribe(AUDIO) == "hi"
    assert len(urls) == 2


def test_stt_other_network_errors_are_not_resent(monkeypatch) -> None:
    urls = _fake_stt_post(monkeypatch, requests.ConnectionError("network down"))
    with pytest.raises(STTError, match="network error"):
        OpenRouterSTT(api_key="k").transcribe(AUDIO)
    assert len(urls) == 1


def _search_post(monkeypatch, post):
    monkeypatch.setattr("memory.config_manager.get_deepseek_key", lambda: "sk-test")
    monkeypatch.setattr(ws.requests, "post", _no_bare_post)
    monkeypatch.setattr(ws._HTTP, "post", post)
    return post


def test_web_search_reuses_one_session(monkeypatch) -> None:
    post = _search_post(monkeypatch, MagicMock(return_value=_resp(200, {"results": []})))
    assert isinstance(ws._HTTP, requests.Session)
    ws._deepseek_search("a", "a")
    ws._deepseek_search("b", "b")
    assert post.call_count == 2


def test_web_search_resends_once_when_the_pooled_connection_was_closed(monkeypatch) -> None:
    good = _resp(200, {"results": [{"title": "T", "url": "https://t", "summary": "s"}]})
    post = _search_post(monkeypatch, MagicMock(side_effect=[_dropped(), good]))
    assert len(ws._deepseek_search("q", "q")) == 1
    assert post.call_count == 2


def _reset_mid_response():
    """What requests raises when the connection is reset after the response began (body cut off)."""
    reset = ConnectionResetError(104, "Connection reset by peer")
    err = requests.exceptions.ChunkedEncodingError(ProtocolError(f"Connection broken: {reset!r}", reset))
    err.__cause__ = reset
    return err


def test_reset_after_the_response_began_is_not_resent() -> None:
    post = MagicMock(side_effect=[_reset_mid_response(), _resp(200, {})])
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        post_once_more_if_dropped(SimpleNamespace(post=post), "https://example.invalid", timeout=1)
    assert post.call_count == 1


def test_closed_pooled_connection_is_resent_once() -> None:
    good = _resp(200, {})
    post = MagicMock(side_effect=[_dropped(), good])
    assert post_once_more_if_dropped(SimpleNamespace(post=post), "https://example.invalid", timeout=1) is good
    assert post.call_count == 2


def test_stt_reset_after_the_response_began_is_not_resent(monkeypatch) -> None:
    urls = _fake_stt_post(monkeypatch, _reset_mid_response(), _Resp(200, {"text": "hi"}))
    with pytest.raises(STTError, match="network error"):
        OpenRouterSTT(api_key="k").transcribe(AUDIO)
    assert len(urls) == 1


def test_session_route_leaves_no_instance_attribute(monkeypatch) -> None:
    from core import llm_client

    sessions = (stt._HTTP, ws._HTTP, llm_client._HTTP)
    assert all("post" in vars(s) for s in sessions)  # conftest routes them through requests.post
    monkeypatch.undo()  # what pytest does after the test
    assert not any("post" in vars(s) for s in sessions)
    assert all(s.post.__func__ is requests.Session.post for s in sessions)
