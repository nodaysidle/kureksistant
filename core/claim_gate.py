"""core/claim_gate.py — does a spoken "done" match what the tools actually did?

Observe-only. The daemon records each tool outcome of the turn, and every
sentence is checked just before it is queued for speech. A first-person claim
of a deletion, a send or a creation that no tool outcome backs is logged to a
JSONL file. Nothing spoken is ever changed here: the log is how the trigger
table is tuned before anything is allowed to rewrite or block speech.

English only: speech input and replies are English (rules.toml `english-only`).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

LOG_PATH = Path.home() / ".local" / "share" / "kurek" / "claim-violations.jsonl"

# shortcut: outcomes are read from the result text because tools return plain
# strings; replace with a structured result once the log shows misclassifications.
_PENDING = re.compile(r"CONFIRMATION_PENDING|confirmation waiting", re.I)
_FAILED = re.compile(
    r"^(error|access denied|refused|protected directory|file not found|could not|i cannot"
    r"|tool '[^']*' (failed|is disabled)|action '[^']*' is not available)|\b(failed|error:)",
    re.I,
)

# Claim class -> (past-tense trigger, tools that can back it: name -> actions or None for any).
_FILE_MAKE = frozenset({"create_file", "create", "touch", "new_file", "make_file", "create_folder",
                        "mkdir", "make_folder", "folder", "write", "write_file", "save", "save_file",
                        "copy", "cp", "move", "mv", "rename", "mv_name", "organize_desktop"})
_FILE_DELETE = frozenset({"delete", "remove", "rm", "unlink"})
_RUN_EXEC = frozenset({"exec", "shell", ""})  # "" = no action given, which run treats as exec
_CODE_WRITE = frozenset({"write", "edit", "build", "auto", "optimize"})
_REMEMBER = frozenset({"remember", ""})  # "" = no action given, which manage_memory treats as remember
CLAIMS: dict[str, tuple[re.Pattern, dict[str, frozenset | None]]] = {
    "DELETED": (
        re.compile(r"\b(deleted|removed|trashed|erased)\b|\bmoved\b[^.]*\btrash\b", re.I),
        {"file_controller": _FILE_DELETE, "run": _RUN_EXEC},
    ),
    "SENT": (
        re.compile(r"\b(sent|messaged|texted|emailed)\b", re.I),
        {"send_message": None, "run": _RUN_EXEC},
    ),
    "CREATED": (
        re.compile(r"\b(saved|wrote|created)\b|\b(?:have|'ve|has|was|been)\s+written\b", re.I),
        {"file_controller": _FILE_MAKE, "run": _RUN_EXEC, "dev_agent": None, "code_helper": _CODE_WRITE,
         "manage_memory": _REMEMBER, "draft_to_clipboard": None},
    ),
}

# A hypothetical, a plan or a refusal just before the verb is not a claim of something done.
_HEDGE = re.compile(
    r"\b(if|would|could|should|will|shall|might|may|let me|want me to|going to|about to|unless|once)\b"
    r"|\b(i'll|i'd|i'm going)\b",
    re.I,
)
_CONTEXT = 40  # characters before the claim verb that can hedge or negate it
_YOU_BEFORE = re.compile(r"\byou(?:'ve|'d)?(?:\s+(?:already|just|have|had))*\s*$", re.I)
_NEGATION = re.compile(r"\b(not|never|nothing|unable|cannot|failed)\b|n't\b", re.I)


def classify(result: object) -> str:
    """ok | failed | timed_out | pending_confirmation, from a tool's result text."""
    text = str(result or "").strip()
    if text.startswith("TIMED OUT"):
        return "timed_out"
    if _PENDING.search(text):
        return "pending_confirmation"
    if _FAILED.search(text):
        return "failed"
    return "ok"


def record(tool: str, args: object, result: object) -> dict:
    """One ledger entry for the turn."""
    return {"tool": tool, "args": args if isinstance(args, dict) else {}, "outcome": classify(result)}


def _binds(claim: str, entry: dict) -> bool:
    actions = CLAIMS[claim][1].get(entry["tool"], False)
    if actions is False:
        return False
    return actions is None or str(entry["args"].get("action", "")).lower().strip() in actions


def _said_by_you(text: str, start: int) -> bool:
    """'the message you sent': a claim about the user's own action is not ours to check."""
    return bool(_YOU_BEFORE.search(text[:start]))


def evaluate(sentence: str, ledger: list[dict]) -> list[dict]:
    """Claims in one sentence that no outcome in this turn's ledger backs."""
    text = sentence.strip()
    if not text or "?" in text:
        return []
    hits = []
    for claim, (pattern, _) in CLAIMS.items():
        m = pattern.search(text)
        if not m or _said_by_you(text, m.start()):
            continue
        before = text[max(0, m.start() - _CONTEXT):m.start()]
        if _HEDGE.search(before) or _NEGATION.search(before):
            continue
        bound = [e for e in ledger if _binds(claim, e)]
        if any(e["outcome"] == "ok" for e in bound):
            continue
        # A delete is parked behind the on-screen confirmation, so "ok" never happens in-turn.
        reason = bound[-1]["outcome"] if bound else "no_tool_call"
        hits.append({"class": claim, "reason": reason})
    return hits


def _log_path() -> Path:
    return Path(os.environ.get("KUREK_CLAIM_LOG") or LOG_PATH)


def _append(line: str) -> None:
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def observe(sentence: str, ledger: list[dict]) -> list[dict]:
    """Check a sentence, log any claim without backing, return the hits. The daemon wraps this call."""
    if os.environ.get("KUREK_CLAIM_GATE", "observe").lower() == "off":
        return []
    hits = evaluate(sentence, ledger)
    for hit in hits:
        _append(json.dumps({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "sentence": sentence.strip(),
            **hit,
            "ledger": [{"tool": e["tool"], "outcome": e["outcome"]} for e in ledger],
        }, ensure_ascii=False))
    return hits
