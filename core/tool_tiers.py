"""Two-tier tool exposure: always-sent schemas plus a `load_tools` loader.

Sending every tool schema on every request costs ~27K characters of prompt
before the user says anything. The tools below were called once or never in 30
days of daemon logs, so their schemas are deferred: the model sees the loader
and pulls a schema in only when it actually needs that tool.
"""
from __future__ import annotations

import re

DEFERRED = frozenset({
    "game_updater",
    "flight_finder",
    "file_processor",
    "youtube_video",
    "send_message",
    "draft_to_clipboard",
    "dream_tool",
    "code_helper",
    "dev_agent",
    "manage_clipboard",
    "desktop_control",
})

LOADER = "load_tools"

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _first_sentence(text: str, limit: int = 120) -> str:
    flat = " ".join((text or "").split())
    head = _SENTENCE_END.split(flat, maxsplit=1)[0]
    return head[:limit]


def split(openai_tools: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """Return (always-sent tools, {name: tool}) in the given order."""
    always: list[dict] = []
    deferred: dict[str, dict] = {}
    for tool in openai_tools:
        name = (tool.get("function") or {}).get("name", "")
        if name in DEFERRED:
            deferred[name] = tool
        else:
            always.append(tool)
    return always, deferred


def loader_tool(deferred: dict[str, dict]) -> dict:
    names = sorted(deferred)
    summary = "; ".join(
        f"{name}: {_first_sentence((deferred[name].get('function') or {}).get('description', ''))}"
        for name in names
    )
    return {
        "type": "function",
        "function": {
            "name": LOADER,
            "description": (
                "Some rarely used tools are not loaded. Call this with the names you need; "
                "they become callable right after. Available: " + summary
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "names": {"type": "array", "items": {"type": "string", "enum": names}},
                },
                "required": ["names"],
            },
        },
    }


def load(requested, deferred: dict[str, dict], turn_tools: list[dict]) -> str:
    """Append requested deferred schemas to turn_tools in place; report what happened."""
    if isinstance(requested, str):
        requested = [requested]
    present = {(tool.get("function") or {}).get("name") for tool in turn_tools}
    loaded: list[str] = []
    unknown: list[str] = []
    for name in requested or []:
        if not isinstance(name, str):
            continue
        if name not in deferred:
            if name not in unknown:
                unknown.append(name)
            continue
        if name in present:
            continue
        present.add(name)
        loaded.append(name)
        turn_tools.append(deferred[name])

    parts: list[str] = []
    if loaded:
        parts.append(f"Loaded: {', '.join(loaded)}. Call them now.")
    if unknown:
        parts.append(f"Unknown tools: {', '.join(unknown)}.")
    return " ".join(parts) if parts else "Nothing to load."
