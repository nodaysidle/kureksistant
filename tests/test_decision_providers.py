"""Offline tests for the multi-provider decision layer (Mouse-1 → Decision-1 → Jev)."""
from __future__ import annotations

import json

import pytest

import core.decision_client as decision_client
import memory.config_manager as config_manager

SECRET = "mouse-test-secret-DO-NOT-LEAK-7c31"


class _Response:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _noul(noul: float, *, abstain: bool | None = None) -> dict:
    answer = {"type": "noul", "noul": noul}
    if abstain is not None:
        answer["mouse"] = {"calibrated": True, "p_top": noul, "abstain": abstain}
    return {"answers": {"needs_screen": answer}}


def _choice(choice: str, *, abstain: bool | None = None) -> dict:
    answer = {
        "type": "choice",
        "choice": choice,
        "confidence": 0.9,
        "probabilities": {choice: 0.9},
    }
    if abstain is not None:
        answer["mouse"] = {"calibrated": True, "p_top": 0.9, "abstain": abstain}
    return {"answers": {"intent": answer}}


def _keys(monkeypatch, *, mouse: str | None = None, orouter: str | None = None, jev: str | None = None):
    monkeypatch.setattr(config_manager, "get_mouse_key", lambda: mouse)
    monkeypatch.setattr(config_manager, "get_openrouter_api_key", lambda: orouter)
    monkeypatch.setattr(config_manager, "get_typesafe_key", lambda: jev)
    monkeypatch.delenv("KUREK_DECISION_TIMEOUT", raising=False)
    monkeypatch.delenv("KUREK_DECISION_BUDGET", raising=False)
    monkeypatch.delenv("KUREK_DECISION_INTENT_BUDGET", raising=False)


def test_mouse_is_preferred_and_answer_is_parsed(monkeypatch) -> None:
    _keys(monkeypatch, mouse=SECRET, orouter="openrouter-key")
    seen = []

    def opener(req, timeout=None):
        seen.append((req.full_url, timeout, json.loads(req.data.decode())))
        return _Response(_noul(0.91))

    prob, engine = decision_client.query_needs_screen("what is on my screen?", opener=opener)

    assert prob == pytest.approx(0.91)
    assert engine == "Mouse-1"
    assert len(seen) == 1
    url, timeout, body = seen[0]
    assert url == decision_client.MOUSE_URL
    assert body["model"] == decision_client.MOUSE_MODEL
    assert body["questions"]["needs_screen"]["instructions"] == decision_client.NEEDS_SCREEN_INSTRUCTIONS
    # Mouse budgets 1.5 s by default; Decision-1 keeps 1.5 s.
    assert timeout == pytest.approx(1.5)


def test_budget_exhaustion_falls_back_without_calling(monkeypatch) -> None:
    _keys(monkeypatch, mouse=SECRET)
    monkeypatch.setenv("KUREK_DECISION_BUDGET", "0.01")

    def opener(*_a, **_k):  # pragma: no cover - must not run
        raise AssertionError("no time left in the budget")

    assert decision_client.query_needs_screen("hi", opener=opener) == (None, "Heuristic")


def test_falls_through_to_decision1(monkeypatch) -> None:
    _keys(monkeypatch, mouse=SECRET, orouter="openrouter-key")
    calls = []

    def opener(req, timeout=None):
        calls.append(req.full_url)
        if req.full_url == decision_client.MOUSE_URL:
            raise TimeoutError("mouse timed out")
        return _Response(_noul(0.8))

    prob, engine = decision_client.query_needs_screen("hi", opener=opener)

    assert prob == pytest.approx(0.8)
    assert engine == "Microsoft-Decision-1"
    assert calls == [decision_client.MOUSE_URL, decision_client.OPENROUTER_DECISIONS_URL]


def test_falls_through_to_jev(monkeypatch) -> None:
    _keys(monkeypatch, mouse=None, orouter=None, jev="jev-key")
    seen = []

    def opener(req, timeout=None):
        seen.append((req.full_url, json.loads(req.data.decode())["model"]))
        return _Response(_noul(0.62))

    prob, engine = decision_client.query_needs_screen("hi", opener=opener)

    assert prob == pytest.approx(0.62)
    assert engine == "TypeSafe Jev"
    assert seen == [(decision_client.TYPESAFE_URL, decision_client.TYPESAFE_MODEL)]


def test_no_keys_makes_no_request(monkeypatch) -> None:
    _keys(monkeypatch)

    def opener(*_a, **_k):  # pragma: no cover - must not run
        raise AssertionError("no provider is configured")

    assert decision_client.query_needs_screen("hi", opener=opener) == (None, "Heuristic")
    assert decision_client.available_providers() == ()


def test_abstain_is_treated_as_no_decision(monkeypatch) -> None:
    _keys(monkeypatch, mouse=SECRET)
    prob, engine = decision_client.query_needs_screen(
        "hi", opener=lambda *_a, **_k: _Response(_noul(0.6, abstain=True))
    )
    assert prob is None
    assert engine == "Heuristic"


def test_unknown_choice_is_rejected(monkeypatch) -> None:
    _keys(monkeypatch, mouse=SECRET)
    intent, engine = decision_client.classify_intent(
        "make it faster", opener=lambda *_a, **_k: _Response(_choice("rewrite_everything"))
    )
    assert intent is None
    assert engine == "Heuristic"


def test_intent_classification_uses_state_context(monkeypatch, tmp_path) -> None:
    _keys(monkeypatch, mouse=SECRET)
    existing = tmp_path / "app.py"
    existing.write_text("print(1)\n", encoding="utf-8")
    seen = {}

    def opener(req, timeout=None):
        seen.update(json.loads(req.data.decode()))
        return _Response(_choice("optimize"))

    intent, engine = decision_client.classify_intent(
        "speed this up", str(existing), "", opener=opener
    )

    assert (intent, engine) == ("optimize", "Mouse-1")
    assert seen["state"]["file_exists"] is True
    assert seen["state"]["has_inline_code"] is False
    assert set(seen["questions"]["intent"]["criteria"]) == set(decision_client.INTENT_LABELS)


def test_timeout_override_applies_to_every_provider(monkeypatch) -> None:
    _keys(monkeypatch, mouse=SECRET)
    monkeypatch.setenv("KUREK_DECISION_TIMEOUT", "2.25")
    seen = []

    def opener(req, timeout=None):
        seen.append(timeout)
        return _Response(_noul(0.5))

    decision_client.query_needs_screen("hi", opener=opener)
    assert seen == [pytest.approx(2.25)]


def test_failure_message_redacts_key(capsys, monkeypatch) -> None:
    _keys(monkeypatch, mouse=SECRET)

    def opener(req, timeout=None):
        raise RuntimeError(f"unauthorized for {SECRET}")

    assert decision_client.query_needs_screen("hi", opener=opener) == (None, "Heuristic")
    out = capsys.readouterr().out
    assert SECRET not in out
    assert "[REDACTED]" in out
    assert "RuntimeError" in out


def test_intent_routing_can_be_disabled(monkeypatch) -> None:
    monkeypatch.delenv("KUREK_DECISION_INTENT", raising=False)
    assert decision_client.intent_routing_enabled() is True
    monkeypatch.setenv("KUREK_DECISION_INTENT", "0")
    assert decision_client.intent_routing_enabled() is False
    monkeypatch.setenv("KUREK_DECISION_INTENT", "off")
    assert decision_client.intent_routing_enabled() is False


def test_intent_budget_is_larger_than_the_prompt_budget(monkeypatch) -> None:
    monkeypatch.delenv("KUREK_DECISION_INTENT_BUDGET", raising=False)
    assert decision_client.intent_budget_s() == pytest.approx(5.0)
    assert decision_client.intent_budget_s() > decision_client.decision_budget_s()
    monkeypatch.setenv("KUREK_DECISION_INTENT_BUDGET", "7.5")
    assert decision_client.intent_budget_s() == pytest.approx(7.5)
    monkeypatch.setenv("KUREK_DECISION_INTENT_BUDGET", "nope")
    assert decision_client.intent_budget_s() == pytest.approx(5.0)


def test_intent_uses_the_intent_budget(monkeypatch) -> None:
    _keys(monkeypatch, mouse=SECRET)
    monkeypatch.setenv("KUREK_DECISION_INTENT_BUDGET", "0.01")

    def opener(*_a, **_k):  # pragma: no cover - budget is spent before the call
        raise AssertionError("no time left in the intent budget")

    assert decision_client.classify_intent("write a parser", opener=opener) == (None, "Heuristic")
