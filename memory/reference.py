"""
memory/reference.py — the reference files Kurek reads but never rewrites.

Hermes USER.md / MEMORY.md (see HERMES_* in .env), ~/MEMORY.md and the legacy
~/ALIGNMENT_SYNTHESIS.md. The prompt gets them capped; recall searches them whole.
"""

from __future__ import annotations

import os
from pathlib import Path

from memory.config_manager import resolve_hermes_memory_dirs

MEMORY_FILE_MAX_CHARS = int(os.environ.get("KUREK_MEMORY_FILE_MAX_CHARS", "6000"))

_HERMES_FILES = ("USER.md", "MEMORY.md")
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
    """Hermes [USER]/[MEMORY], then [MUSE_CORE_MEMORY] and [MUSE_ALIGNMENT_SYNTHESIS], each capped."""
    parts = []
    if hdir := hermes_dir():
        for fname in _HERMES_FILES:
            fpath = hdir / fname
            if fpath.exists():
                try:
                    clean = "\n".join(b.strip() for b in read_capped(fpath).split("§") if b.strip())
                    if clean:
                        parts.append(f"[{fname.replace('.md', '')}]\n{clean}")
                except Exception as e:
                    print(f"[Kurek Hermes] Error reading {fname}: {e}")

    home = Path.home()
    for tag, fpath in (("MUSE_CORE_MEMORY", home / "MEMORY.md"),
                       ("MUSE_ALIGNMENT_SYNTHESIS", home / "ALIGNMENT_SYNTHESIS.md")):
        if fpath.exists():
            try:
                parts.append(f"[{tag}]\n{read_capped(fpath)}")
            except Exception as e:
                print(f"[Kurek Muse] Error reading ~/{fpath.name}: {e}")
    return "\n\n".join(parts)


def entries() -> list[tuple[str, str]]:
    """(label, text) for every Hermes block and every '-' line of ~/MEMORY.md, for recall."""
    found = []
    if hdir := hermes_dir():
        for fname in _HERMES_FILES:
            fpath = hdir / fname
            if fpath.exists():
                try:
                    for block in fpath.read_text(encoding="utf-8").split("§"):
                        found.append((f"Hermes {fname}", " ".join(block.split())))
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
