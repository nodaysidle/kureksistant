"""
memory/reference.py — the reference files Kurek reads but never rewrites.

Hermes USER.md (see HERMES_* in .env) and ~/MEMORY.md. The prompt gets them capped;
recall searches them whole. Hermes MEMORY.md is never read: it is Hermes's own working
memory and instructions, not facts about the user. ~/ALIGNMENT_SYNTHESIS.md is retired
and never read.
"""

from __future__ import annotations

import os
from pathlib import Path

from memory.config_manager import resolve_hermes_memory_dirs

MEMORY_FILE_MAX_CHARS = int(os.environ.get("KUREK_MEMORY_FILE_MAX_CHARS", "6000"))

_HERMES_FILE = "USER.md"
_warned_truncated: set[tuple[Path, int]] = set()


def hermes_dir() -> Path | None:
    """The first configured Hermes memories directory that exists, or None."""
    return next((d for d in resolve_hermes_memory_dirs() if d.exists() and d.is_dir()), None)


def read_capped(path: Path, limit: int = MEMORY_FILE_MAX_CHARS) -> str:
    """Read a markdown memory file, keeping its head and newest tail within limit."""
    text = path.read_text(encoding="utf-8").strip()
    if len(text) <= limit:
        return text
    if (path, len(text)) not in _warned_truncated:
        _warned_truncated.add((path, len(text)))
        print(f"[Kurek Memory] WARNING: {path} is {len(text)} chars, over the {limit} cap; "
              f"the middle is hidden from the model. Raise KUREK_MEMORY_FILE_MAX_CHARS.", flush=True)
    head = limit // 3
    tail = limit - head
    return f"{text[:head]}\n[… older entries omitted …]\n{text[-tail:]}"


def prompt_block() -> str:
    """Hermes [USER], then [MUSE_CORE_MEMORY] (~/MEMORY.md), each capped."""
    parts = []
    if hdir := hermes_dir():
        fpath = hdir / _HERMES_FILE
        if fpath.exists():
            try:
                clean = "\n".join(b.strip() for b in read_capped(fpath).split("§") if b.strip())
                if clean:
                    parts.append(f"[{_HERMES_FILE.replace('.md', '')}]\n{clean}")
            except Exception as e:
                print(f"[Kurek Hermes] Error reading {_HERMES_FILE}: {e}")

    fpath = Path.home() / "MEMORY.md"
    if fpath.exists():
        try:
            parts.append(f"[MUSE_CORE_MEMORY]\n{read_capped(fpath)}")
        except Exception as e:
            print(f"[Kurek Muse] Error reading ~/{fpath.name}: {e}")
    return "\n\n".join(parts)


def entries() -> list[tuple[str, str]]:
    """(label, text) for every Hermes USER.md block and every '-' line of ~/MEMORY.md, for recall."""
    found = []
    if hdir := hermes_dir():
        fpath = hdir / _HERMES_FILE
        if fpath.exists():
            try:
                for block in fpath.read_text(encoding="utf-8").split("§"):
                    found.append((f"Hermes {_HERMES_FILE}", " ".join(block.split())))
            except Exception:
                pass

    reference = Path.home() / "MEMORY.md"
    if reference.exists():
        try:
            for line in reference.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("-"):
                    found.append(("Reference facts", line.strip()))
        except Exception:
            pass
    return found
