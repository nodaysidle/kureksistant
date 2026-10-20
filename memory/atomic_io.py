"""Atomic, private file writes for user data (memory mirrors, api_keys.json)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

PRIVATE_MODE = 0o600


def atomic_write_text(path: str | os.PathLike, text: str, mode: int = PRIVATE_MODE) -> None:
    """Write ``text`` to ``path`` atomically.

    Writes a temp file in the same directory, fsyncs it, chmods it to ``mode``
    and os.replace()s it over the target, so readers never see a truncated
    file and a crash mid-write leaves the previous version intact.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def chmod_private(*paths: str | os.PathLike) -> None:
    """Best-effort chmod 0600 on files that exist (e.g. a SQLite db and its -wal/-shm)."""
    for p in paths:
        try:
            if os.path.exists(p):
                os.chmod(p, PRIVATE_MODE)
        except OSError:
            pass
