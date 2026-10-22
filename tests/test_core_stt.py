"""STTError contract for cloud engines and FallbackSTT behaviour (offline)."""
from __future__ import annotations

import threading

import numpy as np
import pytest
import requests

from core.stt import DeepgramSTT, FallbackSTT, OpenRouterSTT, STTError

AUDIO = (np.sin(np.linspace(0, 100, 16000)) * 0.3).astype(np.float32)


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _engines():
    return [
        OpenRouterSTT(api_key="or-test"),
        DeepgramSTT(api_key="dg-test"),
    ]


@pytest.mark.parametrize("engine", _engines(), ids=["openrouter", "deepgram"])
def test_http_500_raises_stt_error(monkeypatch, engine) -> None:
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(500, text="boom"))
    with pytest.raises(STTError, match="HTTP 500"):
        engine.transcribe(AUDIO)


@pytest.mark.parametrize("engine", _engines(), ids=["openrouter", "deepgram"])
def test_request_exception_raises_stt_error(monkeypatch, engine) -> None:
    def boom(*_a, **_k):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(requests, "post", boom)
    with pytest.raises(STTError, match="network error"):
        engine.transcribe(AUDIO)


@pytest.mark.parametrize("engine", _engines(), ids=["openrouter", "deepgram"])
def test_timeout_raises_stt_error(monkeypatch, engine) -> None:
    def slow(*_a, **_k):
        raise requests.Timeout("read timed out")

    monkeypatch.setattr(requests, "post", slow)
    with pytest.raises(STTError, match="timeout"):
        engine.transcribe(AUDIO)


def test_success_without_speech_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200, {"text": "  "}))
    assert OpenRouterSTT(api_key="k").transcribe(AUDIO) == ""
    monkeypatch.setattr(
        requests, "post",
        lambda *a, **k: _Resp(200, {"results": {"channels": [{"alternatives": [{"transcript": ""}]}]}}),
    )
    assert DeepgramSTT(api_key="k").transcribe(AUDIO) == ""


def test_success_returns_transcript(monkeypatch) -> None:
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200, {"text": " hello there "}))
    assert OpenRouterSTT(api_key="k").transcribe(AUDIO) == "hello there"


class _Failing:
    def transcribe(self, audio, sample_rate=16000):
        raise STTError("primary down")


class _Fixed:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def transcribe(self, audio, sample_rate=16000):
        self.calls += 1
        return self.text


def test_fallback_used_and_built_once() -> None:
    built = []

    def factory():
        eng = _Fixed("from fallback")
        built.append(eng)
        return eng

    stt = FallbackSTT(_Failing(), factory)
    assert stt.transcribe(AUDIO) == "from fallback"
    assert stt.transcribe(AUDIO) == "from fallback"
    assert len(built) == 1
    assert built[0].calls == 2


def test_fallback_not_built_when_primary_ok() -> None:
    def factory():
        raise AssertionError("must not build fallback")

    assert FallbackSTT(_Fixed("primary"), factory).transcribe(AUDIO) == "primary"


def test_fallback_factory_error_raises_stt_error() -> None:
    def factory():
        raise RuntimeError("no model")

    with pytest.raises(STTError, match="no model"):
        FallbackSTT(_Failing(), factory).transcribe(AUDIO)


def test_fallback_transcribe_error_raises_stt_error() -> None:
    class Broken:
        def transcribe(self, audio, sample_rate=16000):
            raise ValueError("bad audio")

    with pytest.raises(STTError, match="bad audio"):
        FallbackSTT(_Failing(), Broken).transcribe(AUDIO)


def test_fallback_lazy_init_is_thread_safe() -> None:
    count = 0
    gate = threading.Event()

    def factory():
        nonlocal count
        count += 1
        gate.wait(0.2)
        return _Fixed("ok")

    stt = FallbackSTT(_Failing(), factory)
    threads = [threading.Thread(target=stt.transcribe, args=(AUDIO,)) for _ in range(8)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join()
    assert count == 1


def test_deepgram_language_is_pinned_in_the_url(monkeypatch) -> None:
    seen = {}

    def post(url, *a, **k):
        seen["url"] = url
        return _Resp(200, {"results": {"channels": [{"alternatives": [{"transcript": "hi"}]}]}})

    monkeypatch.setattr(requests, "post", post)
    assert DeepgramSTT(api_key="k", language="en").transcribe(AUDIO) == "hi"
    assert "language=en" in seen["url"]

    assert DeepgramSTT(api_key="k", language="auto").transcribe(AUDIO) == "hi"
    assert "language=" not in seen["url"]


def test_openrouter_language_is_pinned_in_the_payload(monkeypatch) -> None:
    seen = {}

    def post(_url, *_a, json=None, **_k):
        seen["payload"] = json
        return _Resp(200, {"text": "hi"})

    monkeypatch.setattr(requests, "post", post)
    assert OpenRouterSTT(api_key="k", language="en").transcribe(AUDIO) == "hi"
    assert seen["payload"]["language"] == "en"

    OpenRouterSTT(api_key="k").transcribe(AUDIO)
    assert "language" not in seen["payload"]
