"""Decision layer for Kurek: mouse-1 → Decision-1 → Jev, one request shape.

Every provider accepts ``POST {model, state, questions}`` and answers with
``{"answers": {"<question id>": {...}}}``. Mouse answers additionally carry
``mouse: {calibrated, p_top, abstain}`` when a calibration profile is passed.

Order matters: mouse-1 answers in ~100 ms, Decision-1 has a 1.5 s budget, and
Jev is the last resort. A provider that is not configured is skipped, and a
provider that fails or times out never blocks the daemon path.

Privacy: the prompt text leaves the machine on every call that is made.
Disable screen context with ``KUREK_AUTO_SCREEN=0`` and intent routing with
``KUREK_DECISION_INTENT=0``.
"""
from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

import memory.config_manager as config_manager

NEEDS_SCREEN_INSTRUCTIONS = (
    "Does `prompt` ask about visual UI layout, a compiler error, terminal output, "
    "a bug on screen, what is visible, or code currently in view?"
)

INTENT_INSTRUCTIONS = (
    "Which single coding intent does `request` describe? Judge the request, not the "
    "file contents. `file_exists` says whether `file_path` is on disk; "
    "`has_inline_code` says whether a code snippet was supplied."
)

INTENT_LABELS: dict[str, str] = {
    "write": "Create new code from scratch",
    "edit": "Modify an existing file",
    "explain": "Describe what given code or a file does",
    "run": "Execute an existing file",
    "build": "Write code, run it, and iterate until it works",
    "screen_debug": "Analyze an error currently visible on the user's screen",
    "optimize": "Refactor, clean up, or speed up existing code",
}

MOUSE_URL = "https://api.mouse.dev/v1/decisions"
OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"

MOUSE_MODEL = "mouse-1"
DECISION_MODEL = "microsoft/microsoft-decision-1"
TYPESAFE_MODEL = "jev-latest"

DEFAULT_TIMEOUT_S = 1.5
# Mouse is served from US-East. Measured from a European desktop: ~85 ms TLS,
# ~0.55 s warm end-to-end, with occasional multi-second cold starts. 0.45 s
# always timed out, so the budget is 1.5 s and the call fails open.
PROVIDER_TIMEOUTS = {"mouse": 1.5, "decision1": 1.5, "jev": 2.0}
# Ceiling for one decision across all providers, so a bad network cannot stall
# the daemon (``KUREK_DECISION_BUDGET``).
DEFAULT_BUDGET_S = 2.5
SCREEN_CONTEXT_THRESHOLD = 0.75
DEFAULT_PROVIDER_ORDER = ("mouse", "decision1", "jev")

PROVIDER_LABELS = {
    "mouse": "Mouse-1",
    "decision1": "Microsoft-Decision-1",
    "jev": "TypeSafe Jev",
}

UrlOpen = Callable[..., Any]


@dataclass(frozen=True)
class Decision:
    """One provider's answers, plus which provider produced them."""

    provider: str
    answers: dict[str, Any]
    usage: dict[str, Any] | None = None

    @property
    def engine(self) -> str:
        return PROVIDER_LABELS.get(self.provider, self.provider)


def auto_screen_enabled() -> bool:
    """Return False when ``KUREK_AUTO_SCREEN`` is an explicit opt-out."""
    flag = (os.environ.get("KUREK_AUTO_SCREEN") or "1").strip().lower()
    return flag not in ("0", "false", "no", "off", "disabled")


def intent_routing_enabled() -> bool:
    """Return False when ``KUREK_DECISION_INTENT`` is an explicit opt-out."""
    flag = (os.environ.get("KUREK_DECISION_INTENT") or "1").strip().lower()
    return flag not in ("0", "false", "no", "off", "disabled")


def decision_timeout_s(default: float = DEFAULT_TIMEOUT_S) -> float:
    """Timeout for a decision HTTP call (``KUREK_DECISION_TIMEOUT`` wins)."""
    raw = (os.environ.get("KUREK_DECISION_TIMEOUT") or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return value


def decision_budget_s() -> float:
    """Total wall-clock budget for one decision across providers."""
    raw = (os.environ.get("KUREK_DECISION_BUDGET") or "").strip()
    if not raw:
        return DEFAULT_BUDGET_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_BUDGET_S
    return value if value > 0 else DEFAULT_BUDGET_S


def intent_budget_s() -> float:
    """Budget for intent routing, which sits inside a tool call, not a prompt.

    Screen context blocks the first token, so it keeps the tight 2.5 s budget.
    Intent routing happens after the model already chose the code tool, so it can
    afford the full fallback chain (``KUREK_DECISION_INTENT_BUDGET``).
    """
    raw = (os.environ.get("KUREK_DECISION_INTENT_BUDGET") or "").strip()
    if not raw:
        return 5.0
    try:
        value = float(raw)
    except ValueError:
        return 5.0
    return value if value > 0 else 5.0


# ── provider table ────────────────────────────────────────────────────────────


def _provider_spec(name: str) -> dict[str, Any] | None:
    """URL, model, key and timeout for a provider, or None when unconfigured."""
    if name == "mouse":
        key = config_manager.get_mouse_key()
        url, model = MOUSE_URL, MOUSE_MODEL
    elif name == "decision1":
        key = config_manager.get_openrouter_api_key()
        url, model = OPENROUTER_DECISIONS_URL, DECISION_MODEL
    elif name == "jev":
        key = config_manager.get_typesafe_key()
        url, model = TYPESAFE_URL, TYPESAFE_MODEL
    else:
        raise ValueError(f"Unknown decision provider: {name}")
    if not key:
        return None
    return {
        "name": name,
        "key": key,
        "url": url,
        "model": model,
        "timeout": decision_timeout_s(PROVIDER_TIMEOUTS[name]),
    }


def available_providers(order: tuple[str, ...] = DEFAULT_PROVIDER_ORDER) -> tuple[str, ...]:
    """Configured providers, in preference order."""
    return tuple(name for name in order if _provider_spec(name) is not None)


# ── transport ─────────────────────────────────────────────────────────────────


def _safe_exc_message(exc: BaseException, *, redact: str | None = None) -> str:
    """Short exception summary; never echo secrets, headers, or large bodies."""
    msg = str(exc)
    if redact:
        for token in {redact, redact.strip()}:
            if token and token in msg:
                msg = msg.replace(token, "[REDACTED]")
    if len(msg) > 120:
        msg = msg[:117] + "..."
    return f"{type(exc).__name__}: {msg}"


def _post(
    spec: dict[str, Any],
    state: Any,
    questions: dict[str, Any],
    *,
    timeout: float | None = None,
    opener: UrlOpen | None = None,
) -> dict[str, Any] | None:
    """POST one decision request; return the parsed body or None on failure."""
    body = json.dumps(
        {"model": spec["model"], "state": state, "questions": questions}
    ).encode("utf-8")
    request = urllib.request.Request(
        spec["url"],
        data=body,
        headers={
            "Authorization": f"Bearer {spec['key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with (opener or urllib.request.urlopen)(
            request, timeout=timeout if timeout is not None else spec["timeout"]
        ) as response:
            raw = response.read()
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001 — keep daemon path resilient
        print(
            f"[Kurek Decision Notice] {PROVIDER_LABELS[spec['name']]} failed "
            f"({_safe_exc_message(exc, redact=spec['key'])}); trying next...",
            flush=True,
        )
        return None


def query_decision(
    state: Any,
    questions: dict[str, Any],
    *,
    timeout: float | None = None,
    budget: float | None = None,
    opener: UrlOpen | None = None,
    order: tuple[str, ...] = DEFAULT_PROVIDER_ORDER,
) -> Decision | None:
    """Ask the first provider that answers; None when none is configured/working.

    ``timeout`` caps one HTTP call; ``budget`` caps the whole chain, so a slow
    provider cannot spend the next provider's time.
    """
    deadline = monotonic() + (budget if budget is not None else decision_budget_s())
    for name in order:
        spec = _provider_spec(name)
        if spec is None:
            continue
        remaining = deadline - monotonic()
        if remaining <= 0.05:
            print(
                "[Kurek Decision Notice] decision budget exhausted; using the "
                "heuristic path",
                flush=True,
            )
            return None
        call_timeout = min(timeout if timeout is not None else spec["timeout"], remaining)
        payload = _post(spec, state, questions, timeout=call_timeout, opener=opener)
        if payload is None:
            continue
        answers = payload.get("answers")
        if not isinstance(answers, dict) or not answers:
            print(
                f"[Kurek Decision Notice] {PROVIDER_LABELS[name]} returned no answers; "
                "trying next...",
                flush=True,
            )
            continue
        usage = payload.get("usage")
        return Decision(
            provider=name,
            answers=answers,
            usage=usage if isinstance(usage, dict) else None,
        )
    return None


# ── answer parsing ────────────────────────────────────────────────────────────


def _noul_value(answer: Any) -> float | None:
    """Extract a probability from a ``noul`` answer object."""
    if not isinstance(answer, dict):
        return None
    noul = answer.get("noul")
    # bool is a subclass of int — reject True/False explicitly.
    if isinstance(noul, bool) or not isinstance(noul, (int, float)):
        return None
    value = float(noul)
    if value < 0.0 or value > 1.0:
        return None
    return value


def parse_needs_screen_noul(payload: Any) -> float | None:
    """Extract ``answers.needs_screen.noul`` when it is a number in ``[0, 1]``."""
    if not isinstance(payload, dict):
        return None
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return None
    return _noul_value(answers.get("needs_screen"))


def noul_answer(decision: Decision | None, key: str = "needs_screen") -> float | None:
    """Probability for a noul question, or None when missing or abstained."""
    if decision is None:
        return None
    answer = decision.answers.get(key)
    value = _noul_value(answer)
    if value is None:
        return None
    if _abstained(answer):
        return None
    return value


def choice_answer(decision: Decision | None, key: str, options: dict[str, Any]) -> str | None:
    """Picked option for a choice question, or None when missing/abstained/unknown."""
    if decision is None:
        return None
    answer = decision.answers.get(key)
    if not isinstance(answer, dict) or _abstained(answer):
        return None
    choice = answer.get("choice")
    if not isinstance(choice, str) or choice not in options:
        return None
    return choice


def _abstained(answer: Any) -> bool:
    if not isinstance(answer, dict):
        return False
    mouse = answer.get("mouse")
    return bool(isinstance(mouse, dict) and mouse.get("abstain") is True)


# ── call sites ────────────────────────────────────────────────────────────────


def query_openrouter_needs_screen(
    prompt: str,
    api_key: str,
    *,
    timeout: float | None = None,
    urlopen: UrlOpen | None = None,
) -> float | None:
    """Call OpenRouter Decision-1 for ``needs_screen``; return P in [0, 1] or None.

    Kept as the explicit Decision-1 entry point (tests and callers that already
    hold a key). Prefer :func:`query_needs_screen`, which falls back across
    providers.
    """
    if not api_key:
        return None
    spec = {
        "name": "decision1",
        "key": api_key,
        "url": OPENROUTER_DECISIONS_URL,
        "model": DECISION_MODEL,
        "timeout": DEFAULT_TIMEOUT_S,
    }
    payload = _post(
        spec,
        {"prompt": prompt},
        {"needs_screen": {"type": "noul", "instructions": NEEDS_SCREEN_INSTRUCTIONS}},
        timeout=timeout,
        opener=urlopen,
    )
    return parse_needs_screen_noul(payload)


def query_needs_screen(
    prompt: str,
    *,
    timeout: float | None = None,
    opener: UrlOpen | None = None,
    order: tuple[str, ...] = DEFAULT_PROVIDER_ORDER,
) -> tuple[float | None, str]:
    """P(needs screen context) and the engine that answered; ("Heuristic", None) when none."""
    decision = query_decision(
        {"prompt": prompt},
        {"needs_screen": {"type": "noul", "instructions": NEEDS_SCREEN_INSTRUCTIONS}},
        timeout=timeout,
        opener=opener,
        order=order,
    )
    prob = noul_answer(decision)
    if prob is None:
        return None, "Heuristic"
    return prob, decision.engine


def classify_intent(
    description: str,
    file_path: str = "",
    code: str = "",
    *,
    timeout: float | None = None,
    budget: float | None = None,
    opener: UrlOpen | None = None,
    order: tuple[str, ...] = DEFAULT_PROVIDER_ORDER,
) -> tuple[str | None, str]:
    """Pick a coding intent; (intent, engine) or (None, "Heuristic") when unsure."""
    state = {
        "request": description,
        "file_path": file_path or None,
        "file_exists": bool(file_path) and os.path.isfile(file_path),
        "has_inline_code": bool(code),
    }
    decision = query_decision(
        state,
        {
            "intent": {
                "type": "choice",
                "instructions": INTENT_INSTRUCTIONS,
                "criteria": INTENT_LABELS,
            }
        },
        timeout=timeout,
        budget=budget if budget is not None else intent_budget_s(),
        opener=opener,
        order=order,
    )
    intent = choice_answer(decision, "intent", INTENT_LABELS)
    if intent is None:
        return None, "Heuristic"
    return intent, decision.engine
