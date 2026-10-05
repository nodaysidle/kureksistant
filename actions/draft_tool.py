"""
actions/draft_tool.py — Voice-to-clipboard markdown drafter for Kurek.
Auto-discovered by core/action_loader.py.

Capabilities:
  • 'draft': Formats dictated text into conventional commit messages, GitHub PRs,
             issues, docstrings, or markdown notes, and copies directly into wl-clipboard.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ICON_PATH = "/home/arch/.local/share/icons/kurek.png"


def _copy_to_system_clipboard(text: str) -> bool:
    """Copies text to Wayland wl-copy, X11 xclip, or macOS pbcopy."""
    if shutil.which("wl-copy"):
        try:
            subprocess.run(["wl-copy", text], check=True, timeout=2)
            return True
        except Exception:
            pass
    if sys.platform == "darwin" and shutil.which("pbcopy"):
        try:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=2)
            return True
        except Exception:
            pass
    if shutil.which("xclip"):
        try:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode("utf-8"), check=True, timeout=2)
            return True
        except Exception:
            pass
    return False


def _format_draft(text: str, kind: str, title: str | None = None) -> str:
    kind = kind.lower().strip()
    raw = text.strip()

    if kind == "commit":
        # Conventional Commit syntax
        prefix = "feat"
        lower_raw = raw.lower()
        if any(w in lower_raw for w in ("fix", "bug", "error", "patch", "crash")):
            prefix = "fix"
        elif any(w in lower_raw for w in ("refactor", "clean", "simplify", "reorganize")):
            prefix = "refactor"
        elif any(w in lower_raw for w in ("doc", "readme", "comment")):
            prefix = "docs"
        elif any(w in lower_raw for w in ("test", "spec", "coverage")):
            prefix = "test"
        elif any(w in lower_raw for w in ("perf", "speed", "memory", "optimize")):
            prefix = "perf"

        header_subject = title or raw.splitlines()[0]
        # Remove any leading conversational filler
        header_subject = re.sub(r"^(draft commit|commit|make a commit message for|feat:|fix:)\s*", "", header_subject, flags=re.IGNORECASE).strip()
        if header_subject:
            header_subject = header_subject[0].lower() + header_subject[1:]

        formatted = f"{prefix}: {header_subject}\n\n"
        body_lines = [line.strip("- •* ") for line in raw.splitlines() if line.strip()]
        if len(body_lines) > 1:
            formatted += "\n".join(f"- {l}" for l in body_lines[1:]) + "\n"
        else:
            formatted += f"- {raw}\n"
        return formatted.strip()

    if kind in ("pr", "pull_request"):
        t = title or "Pull Request Summary"
        return f"""## {t}

### Summary
{raw}

### Key Changes
- {raw}

### Verification
- Manual verification passed.
- All unit and integration tests passing.
"""

    if kind == "issue":
        t = title or "Issue Description"
        return f"""## {t}

### Problem Statement
{raw}

### Proposed Solution
- Address reported issue according to architecture specifications.

### Steps to Reproduce / Context
- Observed in standard workspace environment.
"""

    if kind == "docstring":
        return f'"""\n{raw}\n\nReturns:\n    None\n"""'

    # Default: markdown note / snippet
    header = f"### {title}\n\n" if title else ""
    return f"{header}{raw}\n"


def draft_to_clipboard(parameters: dict, **kwargs) -> str:
    params = parameters or {}
    text = params.get("text", "").strip()
    kind = params.get("kind", "commit").lower().strip()
    title = params.get("title", "").strip() or None

    if not text:
        return "Please provide the text or description to draft."

    formatted = _format_draft(text, kind, title)
    copied = _copy_to_system_clipboard(formatted)

    if not copied:
        return f"Draft formatted, but could not find wl-copy/pbcopy/xclip to copy to clipboard:\n\n{formatted}"

    # Also register in clipboard manager history for pinning/recall
    try:
        from actions.clipboard_tool import ClipboardStore
        store = ClipboardStore()
        store.add_item(formatted, source="draft_tool", pinned=True, title=f"Draft: {kind}")
    except Exception:
        pass

    # Desktop Notification
    try:
        preview = formatted.splitlines()[0][:60]
        subprocess.run(
            ["notify-send", "-t", "3000", "-i", ICON_PATH, f"📋 {kind.capitalize()} Draft Copied", preview],
            timeout=2,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    first_line = formatted.splitlines()[0]
    return f"Draft copied to clipboard: '{first_line}'. Ready to paste with Ctrl+V."


TOOL = {
    "name": "draft_to_clipboard",
    "description": "Formats dictated text into conventional commit messages, GitHub PR descriptions, issue reports, docstrings, or markdown notes and instantly copies directly to the Wayland system clipboard (wl-copy) for instant pasting with Ctrl+V.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "kind": {
                "type": "STRING",
                "description": "Type of draft: 'commit' (conventional commit), 'pr' (GitHub pull request), 'issue' (GitHub issue), 'docstring', or 'note'",
                "enum": ["commit", "pr", "issue", "docstring", "note"]
            },
            "text": {
                "type": "STRING",
                "description": "Raw dictated text, notes, or instructions to format and copy"
            },
            "title": {
                "type": "STRING",
                "description": "Optional title or headline"
            }
        },
        "required": ["text"]
    },
    "handler": draft_to_clipboard,
}
