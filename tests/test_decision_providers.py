"""Offline tests for the decision layer (Jev → Decision-1 on OpenRouter Decisions)."""
from __future__ import annotations

import json
import urllib.request

import pytest

import core.decision_client as decision_client
import memory.config_manager as config_manager

SECRET = "or-decision-secret-DO-NOT-LEAK-7c31"


class _Response:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _noul(noul) -> dict:
    return {"answers": {"needs_screen": {"type": "noul", "noul": noul}}}


def _choice(choice: str, key: str = "intent", confidence=0.9) -> dict:
    answer = {"type": "choice", "choice": choice, "probabilities": {choice: confidence}}
    if confidence is not None:
        answer["confidence"] = confidence
    return {"answers": {key: answer}}


def _keys(monkeypatch, orouter: str | None = SECRET):
    monkeypatch.setattr(config_manager, "get_openrouter_api_key", lambda: orouter)
    monkeypatch.delenv("KUREK_DECISION_TIMEOUT", raising=False)
    monkeypatch.delenv("KUREK_DECISION_BUDGET", raising=False)
    monkeypatch.delenv("KUREK_DECISION_INTENT_BUDGET", raising=False)


def _model(req) -> str:
    return json.loads(req.data.decode())["model"]


def test_jev_is_preferred_and_answer_is_parsed(monkeypatch) -> None:
    _keys(monkeypatch)
    seen = []

    def opener(req, timeout=None):
        seen.append((req.full_url, timeout, json.loads(req.data.decode())))
        return _Response(_noul(0.91))

    prob, engine = decision_client.query_needs_screen("what is on my screen?", opener=opener)

    assert prob == pytest.approx(0.91)
    assert engine == "TypeSafe Jev"
    assert len(seen) == 1
    url, timeout, body = seen[0]
    assert url == decision_client.OPENROUTER_DECISIONS_URL
    assert body["model"] == "typesafe/jev-1.14"
    assert body["questions"]["needs_screen"]["type"] == "noul"
    assert body["questions"]["needs_screen"]["instructions"] == decision_client.NEEDS_SCREEN_INSTRUCTIONS
    assert timeout == pytest.approx(1.5)


def test_jev_is_first_for_coding_intent(monkeypatch) -> None:
    _keys(monkeypatch)
    models = []

    def opener(req, timeout=None):
        models.append(_model(req))
        return _Response(_choice("build"))

    assert decision_client.classify_intent("make a snake game and run it", opener=opener) == (
        "build",
        "TypeSafe Jev",
    )
    assert models == [decision_client.JEV_MODEL]


def test_budget_exhaustion_falls_back_without_calling(monkeypatch) -> None:
    _keys(monkeypatch)
    monkeypatch.setenv("KUREK_DECISION_BUDGET", "0.01")

    def opener(*_a, **_k):  # pragma: no cover - must not run
        raise AssertionError("no time left in the budget")

    assert decision_client.query_needs_screen("hi", opener=opener) == (None, "Heuristic")


def test_decision1_is_used_only_when_jev_fails(monkeypatch) -> None:
    _keys(monkeypatch)
    models = []

    def opener(req, timeout=None):
        models.append(_model(req))
        if _model(req) == decision_client.JEV_MODEL:
            raise TimeoutError("jev timed out")
        return _Response(_noul(0.8))

    prob, engine = decision_client.query_needs_screen("hi", opener=opener)

    assert prob == pytest.approx(0.8)
    assert engine == "Microsoft-Decision-1"
    assert models == [decision_client.JEV_MODEL, decision_client.DECISION_MODEL]


@pytest.mark.parametrize(
    "bad",
    [
        _noul(1.7),
        _noul(-0.2),
        _noul(float("nan")),
        _noul(True),
        _noul("high"),
        {"answers": {}},
        {"answers": {"other": {"noul": 0.5}}},
        {"nope": 1},
    ],
)
def test_invalid_jev_noul_triggers_fallback(monkeypatch, bad) -> None:
    _keys(monkeypatch)
    models = []

    def opener(req, timeout=None):
        models.append(_model(req))
        return _Response(bad if _model(req) == decision_client.JEV_MODEL else _noul(0.3))

    assert decision_client.query_needs_screen("hi", opener=opener) == (
        pytest.approx(0.3),
        "Microsoft-Decision-1",
    )
    assert models == [decision_client.JEV_MODEL, decision_client.DECISION_MODEL]


def test_unknown_jev_choice_triggers_fallback(monkeypatch) -> None:
    _keys(monkeypatch)

    def opener(req, timeout=None):
        if _model(req) == decision_client.JEV_MODEL:
            return _Response(_choice("rewrite_everything"))
        return _Response(_choice("optimize"))

    assert decision_client.classify_intent("make it faster", opener=opener) == (
        "optimize",
        "Microsoft-Decision-1",
    )


def test_choice_without_confidence_triggers_fallback(monkeypatch) -> None:
    _keys(monkeypatch)

    def opener(req, timeout=None):
        if _model(req) == decision_client.JEV_MODEL:
            return _Response(_choice("run", confidence=None))
        return _Response(_choice("explain"))

    assert decision_client.classify_intent("run it", opener=opener) == (
        "explain",
        "Microsoft-Decision-1",
    )


def test_choice_below_confidence_floor_uses_local_fallback(monkeypatch) -> None:
    _keys(monkeypatch)
    floor = decision_client.CHOICE_CONFIDENCE_FLOOR
    low = _Response(_choice("run", confidence=floor - 0.05))
    assert decision_client.classify_intent("run it", opener=lambda *_a, **_k: low) == (None, "Heuristic")
    at = _Response(_choice("run", confidence=floor))
    assert decision_client.classify_intent("run it", opener=lambda *_a, **_k: at) == ("run", "TypeSafe Jev")


def test_both_providers_failing_returns_heuristic(monkeypatch) -> None:
    _keys(monkeypatch)
    calls = []

    def opener(req, timeout=None):
        calls.append(_model(req))
        return _Response(_choice("rewrite_everything"))

    assert decision_client.classify_intent("make it faster", opener=opener) == (None, "Heuristic")
    assert calls == [decision_client.JEV_MODEL, decision_client.DECISION_MODEL]


def test_no_keys_makes_no_request(monkeypatch) -> None:
    _keys(monkeypatch, orouter=None)

    def opener(*_a, **_k):  # pragma: no cover - must not run
        raise AssertionError("no provider is configured")

    assert decision_client.query_needs_screen("hi", opener=opener) == (None, "Heuristic")
    assert decision_client.classify_intent("write a parser", opener=opener) == (None, "Heuristic")
    assert decision_client.available_providers() == ()


def test_intent_classification_uses_state_context(monkeypatch, tmp_path) -> None:
    _keys(monkeypatch)
    existing = tmp_path / "app.py"
    existing.write_text("print(1)\n", encoding="utf-8")
    seen = {}

    def opener(req, timeout=None):
        seen.update(json.loads(req.data.decode()))
        return _Response(_choice("optimize"))

    intent, engine = decision_client.classify_intent(
        "speed this up", str(existing), "", opener=opener
    )

    assert (intent, engine) == ("optimize", "TypeSafe Jev")
    assert seen["state"]["file_exists"] is True
    assert seen["state"]["has_inline_code"] is False
    assert seen["questions"]["intent"]["type"] == "choice"
    assert set(seen["questions"]["intent"]["criteria"]) == set(decision_client.INTENT_LABELS)


def test_timeout_override_applies_to_every_provider(monkeypatch) -> None:
    _keys(monkeypatch)
    monkeypatch.setenv("KUREK_DECISION_TIMEOUT", "2.25")
    monkeypatch.setenv("KUREK_DECISION_BUDGET", "10")
    seen = []

    def opener(req, timeout=None):
        seen.append(timeout)
        if _model(req) == decision_client.JEV_MODEL:
            raise TimeoutError("jev timed out")
        return _Response(_noul(0.5))

    decision_client.query_needs_screen("hi", opener=opener)
    assert seen == [pytest.approx(2.25), pytest.approx(2.25)]


def test_failure_message_redacts_key(capsys, monkeypatch) -> None:
    _keys(monkeypatch)

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
    _keys(monkeypatch)
    monkeypatch.setenv("KUREK_DECISION_INTENT_BUDGET", "0.01")

    def opener(*_a, **_k):  # pragma: no cover - budget is spent before the call
        raise AssertionError("no time left in the intent budget")

    assert decision_client.classify_intent("write a parser", opener=opener) == (None, "Heuristic")


# ── code_helper._detect_intent ────────────────────────────────────────────────


def _forbid_gemini(monkeypatch):
    import actions.code_helper as code_helper

    def boom(*_a, **_k):  # pragma: no cover - must not run
        raise AssertionError("Gemini must not be asked for a typed decision")

    monkeypatch.setattr(code_helper, "_get_gemini", boom)
    return code_helper


def test_detect_intent_uses_jev_and_never_gemini(monkeypatch) -> None:
    _keys(monkeypatch)
    code_helper = _forbid_gemini(monkeypatch)
    monkeypatch.delenv("KUREK_DECISION_INTENT", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Response(_choice("explain")))

    assert code_helper._detect_intent("what does this do", "", "print(1)") == "explain"


def test_detect_intent_structural_fallback_when_providers_fail(monkeypatch, tmp_path) -> None:
    _keys(monkeypatch)
    code_helper = _forbid_gemini(monkeypatch)
    monkeypatch.delenv("KUREK_DECISION_INTENT", raising=False)

    def down(*_a, **_k):
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    existing = tmp_path / "app.py"
    existing.write_text("print(1)\n", encoding="utf-8")

    assert code_helper._detect_intent("change the greeting", str(existing), "") == "edit"
    assert code_helper._detect_intent("make a parser", "", "") == "write"


def test_detect_intent_opt_out_makes_no_request(monkeypatch) -> None:
    _keys(monkeypatch)
    code_helper = _forbid_gemini(monkeypatch)
    monkeypatch.setenv("KUREK_DECISION_INTENT", "0")

    def forbidden(*_a, **_k):  # pragma: no cover - must not run
        raise AssertionError("KUREK_DECISION_INTENT=0 must not touch the network")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    assert code_helper._detect_intent("explain this", "", "x = 1") == "explain"


# ── dev_agent._classify_error ────────────────────────────────────────────────


@pytest.fixture
def dev_agent(monkeypatch):
    import actions.dev_agent as dev_agent

    dev_agent._ERROR_JUDGMENTS.clear()
    monkeypatch.delenv("KUREK_DECISION_INTENT", raising=False)
    yield dev_agent
    dev_agent._ERROR_JUDGMENTS.clear()


def test_unambiguous_python_output_needs_no_model(monkeypatch, dev_agent) -> None:
    _keys(monkeypatch)

    def forbidden(*_a, **_k):  # pragma: no cover - must not run
        raise AssertionError("unambiguous output must not reach the network")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    assert dev_agent._classify_error("ModuleNotFoundError: No module named 'pygame'") == "dependency_error"
    assert dev_agent._classify_error("  File x.py\nSyntaxError: invalid syntax") == "syntax_error"
    assert dev_agent._classify_error("Traceback (most recent call last):\nKeyError: 'a'") == "runtime_error"
    assert dev_agent._classify_error("Score: 42\nDone.") == "none"


def test_ambiguous_output_is_judged_by_jev(monkeypatch, dev_agent) -> None:
    _keys(monkeypatch)
    models = []

    def opener(req, timeout=None):
        models.append(_model(req))
        return _Response(_choice("none", key="error"))

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    assert dev_agent._classify_error("Testing error: handling... all 12 cases passed") == "none"
    assert models == [decision_client.JEV_MODEL]


def test_ambiguous_output_keeps_local_fallback(monkeypatch, dev_agent) -> None:
    _keys(monkeypatch, orouter=None)
    assert dev_agent._classify_error("error: something odd happened") == "runtime_error"


def test_error_output_is_redacted_before_the_request(monkeypatch, dev_agent) -> None:
    _keys(monkeypatch)
    leaked = "sk-" + "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
    bodies = []

    def opener(req, timeout=None):
        bodies.append(req.data.decode())
        return _Response(_choice("runtime_error", key="error"))

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    out = f"connecting with DEEPSEEK_API_KEY={leaked}\nerror: 401 unauthorized"
    assert dev_agent._classify_error(out) == "runtime_error"
    assert bodies and leaked not in bodies[0]
    assert "401 unauthorized" in json.loads(bodies[0])["state"]["output"]


def test_low_confidence_none_does_not_pass_a_build(monkeypatch, dev_agent) -> None:
    _keys(monkeypatch)
    unsure = _Response(_choice("none", key="error", confidence=0.55))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: unsure)
    out = "error: assertion mismatch in step 3"
    assert dev_agent._classify_error(out) == "runtime_error"
    assert dev_agent._has_error(out, "python main.py") is True


def test_one_model_call_per_output_and_outage_is_retried(monkeypatch, dev_agent) -> None:
    _keys(monkeypatch)
    calls = []
    state = {"up": False}

    def opener(req, timeout=None):
        calls.append(_model(req))
        if not state["up"]:
            raise OSError("network down")
        return _Response(_choice("none", key="error"))

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    out = "Testing error: handling... all 12 cases passed"
    assert dev_agent._classify_error(out) == "runtime_error"  # outage → local fallback, not cached
    state["up"] = True
    calls.clear()
    assert dev_agent._classify_error(out) == "none"
    assert dev_agent._classify_error(out) == "none"
    assert calls == [decision_client.JEV_MODEL]
