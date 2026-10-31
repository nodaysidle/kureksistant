"""
core/proposals.py — rule candidates for the user to review.

The nightly dream cycle and Jev triage only ever *propose* here. Nothing in
this file is read back into a prompt. Each proposal is one of two kinds:

- a rule (how Kurek should behave): it becomes a rule when the user copies it
  into rules.toml (core/rules.py) and marks it approved by hand;
- a fact (something true about the user): on approval it is saved to the
  learned-memory ledger, never to rules.toml.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path

PROPOSALS_PATH = Path.home() / "kurek_proposed_rules.md"
MAX_PENDING = 50
MAX_TEXT_CHARS = 300

KINDS = ("rule", "fact")

_lock = threading.Lock()
# The status may carry a note, e.g. "rejected (duplicate of never-lie, 2026-10-28)".
_ENTRY = re.compile(r"^## (P-\d{4}-\d{2}-\d{2}-\d{2}) · (\w+)\b.*$", re.MULTILINE)


def _one_line(text: str) -> str:
    """Collapse to a single bounded line so source text can't forge entry headings."""
    return " ".join(str(text).split())[:MAX_TEXT_CHARS]


def propose(proposed_by: str, rule: str, *, source: str, heard: str | None = None,
            path: Path | None = None, kind: str = "rule") -> bool:
    """Append a pending proposal. False when empty, duplicate or the queue is full."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    path = path or PROPOSALS_PATH
    rule = _one_line(rule).lstrip("-*• ").strip()
    if not rule:
        return False

    with _lock:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        folded = existing.casefold()
        if any(f"- **proposed {k}:** {rule}".casefold() in folded for k in KINDS):
            return False
        entries = _ENTRY.findall(existing)
        if sum(1 for _, status in entries if status == "pending") >= MAX_PENDING:
            return False

        today = datetime.now().strftime("%Y-%m-%d")
        n = sum(1 for pid, _ in entries if pid.startswith(f"P-{today}-")) + 1
        lines = [f"## P-{today}-{n:02d} · pending", f"- **Proposed by:** {_one_line(proposed_by)}"]
        if heard:
            lines.append(f'- **Heard (verbatim):** "{_one_line(heard)}"')
        lines += [f"- **Type:** {kind}", f"- **Proposed {kind}:** {rule}",
                  f"- **Source:** {_one_line(source)}"]

        header = "" if existing else (
            "# Proposed rules\n\n"
            "Nothing here reaches Kurek until you approve it: a rule is copied into "
            "~/.config/kurek/rules.toml, a fact is saved to learned memory. Then change "
            "`pending` to `approved` (or `rejected`).\n\n"
        )
        sep = "" if not existing or existing.endswith("\n\n") else "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(header + sep + "\n".join(lines) + "\n\n")
        return True
