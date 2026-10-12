"""Optional OpenRouter Decision-1 client for auto screen-context gating.

When ``OPENROUTER_API_KEY`` is set and auto-screen is enabled, each user prompt
text is sent to OpenRouter's alpha decisions API (privacy: prompt leaves the
machine). TypeSafe Jev remains the fallback when Decision-1 is unavailable.
"""
from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable
from typing import Any

NEEDS_SCREEN_INSTRUCTIONS = (
    "Does `prompt` ask about visual UI layout, a compiler error, terminal output, "
    "a bug on screen, what is visible, or code currently in view?"
)

OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DECISION_MODEL = "microsoft/microsoft-decision-1"
DEFAULT_TIMEOUT_S = 1.5
SCREEN_CONTEXT_THRESHOLD = 0.75

UrlOpen = Callable[..., Any]


def auto_screen_enabled() -> bool:
    """Return False when ``KUREK_AUTO_SCREEN`` is an explicit opt-out."""
    flag = (os.environ.get("KUREK_AUTO_SCREEN") or "1").strip().lower()
    return flag not in ("0", "false", "no", "off", "disabled")


def decision_timeout_s() -> float:
    """Timeout for Decision-1 HTTP (default 1.5s; ``KUREK_DECISION_TIMEOUT``)."""
    raw = (os.environ.get("KUREK_DECISION_TIMEOUT") or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_S
    if value <= 0:
        return DEFAULT_TIMEOUT_S
    return value


def parse_needs_screen_noul(payload: Any) -> float | None:
    """Extract ``answers.needs_screen.noul`` when it is a number in ``[0, 1]``."""
    if not isinstance(payload, dict):
        return None
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return None
    needs = answers.get("needs_screen")
    if not isinstance(needs, dict):
        return None
    noul = needs.get("noul")
    # bool is a subclass of int — reject True/False explicitly.
    if isinstance(noul, bool) or not isinstance(noul, (int, float)):
        return None
    value = float(noul)
    if value < 0.0 or value > 1.0:
        return None
    return value


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


def query_openrouter_needs_screen(
    prompt: str,
    api_key: str,
    *,
    timeout: float | None = None,
    urlopen: UrlOpen | None = None,
) -> float | None:
    """Call OpenRouter Decision-1 for ``needs_screen``; return P in [0, 1] or None.

    Never logs the API key, response headers, or response bodies — only exception
    type and a short redacted message on failure.
    """
    if not api_key:
        return None

    timeout_s = DEFAULT_TIMEOUT_S if timeout is None else timeout
    opener = urlopen or urllib.request.urlopen
    body = json.dumps(
        {
            "model": DECISION_MODEL,
            "state": {"prompt": prompt},
            "questions": {
                "needs_screen": {
                    "type": "noul",
                    "instructions": NEEDS_SCREEN_INSTRUCTIONS,
                }
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        OPENROUTER_DECISIONS_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with opener(request, timeout=timeout_s) as response:
            raw = response.read()
        data = json.loads(raw.decode("utf-8"))
        return parse_needs_screen_noul(data)
    except Exception as exc:  # noqa: BLE001 — keep daemon path resilient
        print(
            f"[Kurek Decision Notice] OpenRouter Decision-1 failed "
            f"({_safe_exc_message(exc, redact=api_key)}); falling back...",
            flush=True,
        )
        return None
