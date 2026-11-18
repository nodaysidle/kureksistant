"""memory/paths.py — XDG Base Directory paths and one-shot legacy migration.

Live runtime files belong under $XDG_DATA_HOME/kurek and $XDG_STATE_HOME/kurek,
never inside the repository tree. Migration from legacy repo-local paths is
explicit and must only be invoked at daemon/CLI startup (never from tests or
``ledger._connect()``).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from memory.atomic_io import PRIVATE_MODE

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _xdg_dir(env_var: str, *fallback_parts: str) -> Path:
    raw = os.environ.get(env_var, "").strip()
    base = (
        Path(os.path.expanduser(raw)) if raw else Path.home().joinpath(*fallback_parts)
    )
    return base / "kurek"


def data_dir() -> Path:
    """Return the per-user data directory (``$XDG_DATA_HOME/kurek``)."""
    return _xdg_dir("XDG_DATA_HOME", ".local", "share")


def state_dir() -> Path:
    """Return the per-user state directory (``$XDG_STATE_HOME/kurek``)."""
    return _xdg_dir("XDG_STATE_HOME", ".local", "state")


def db_path() -> Path:
    """Return the path to the learned-memory SQLite database."""
    return data_dir() / "kurek_memory.db"


def history_path() -> Path:
    """Return the path to the rolling conversation history JSON file."""
    return state_dir() / "kurek_history.json"


def clipboard_path() -> Path:
    """Return the path to the clipboard history JSON file."""
    return data_dir() / "clipboard_history.json"


def _atomic_copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(dst.parent), prefix=f".{dst.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as dst_f, src.open("rb") as src_f:
            shutil.copyfileobj(src_f, dst_f)
            dst_f.flush()
            os.fsync(dst_f.fileno())
        os.chmod(tmp_path, PRIVATE_MODE)
        os.replace(tmp_path, dst)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _backup_sqlite(legacy: Path, target: Path) -> bool:
    """Copy a SQLite DB (merging any -wal) to ``target`` via sqlite3 backup."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    src_conn: sqlite3.Connection | None = None
    dst_conn: sqlite3.Connection | None = None
    try:
        src_conn = sqlite3.connect(str(legacy), timeout=10.0)
        dst_conn = sqlite3.connect(str(tmp_path), timeout=10.0)
        src_conn.backup(dst_conn)
        dst_conn.close()
        dst_conn = None
        src_conn.close()
        src_conn = None
        with tmp_path.open("rb") as f:
            os.fsync(f.fileno())
        os.chmod(tmp_path, PRIVATE_MODE)
        os.replace(tmp_path, target)
        return True
    except sqlite3.DatabaseError:
        if dst_conn is not None:
            dst_conn.close()
        if src_conn is not None:
            src_conn.close()
        tmp_path.unlink(missing_ok=True)
        return False
    except BaseException:
        if dst_conn is not None:
            dst_conn.close()
        if src_conn is not None:
            src_conn.close()
        tmp_path.unlink(missing_ok=True)
        raise


def migrate_legacy_file(legacy: Path, target: Path) -> bool:
    """Safely move ``legacy`` to ``target`` if ``legacy`` exists and ``target`` does not.

    For SQLite databases (``.db`` or files with ``-wal``/``-shm`` siblings),
    checkpoints/merges WAL state cleanly using SQLite's online backup API before
    atomically replacing ``target`` and removing the legacy file(s). Returns
    ``True`` if migration ran, ``False`` if skipped.
    """
    legacy = Path(legacy)
    target = Path(target)
    if not legacy.exists() or not legacy.is_file() or target.exists():
        return False

    wal = Path(f"{legacy}-wal")
    shm = Path(f"{legacy}-shm")
    is_sqlite = legacy.suffix == ".db" or wal.exists() or shm.exists()

    migrated_sqlite = is_sqlite and _backup_sqlite(legacy, target)
    if not migrated_sqlite:
        _atomic_copy_file(legacy, target)
        for suffix, sibling in (("-wal", wal), ("-shm", shm)):
            sibling_target = Path(f"{target}{suffix}")
            if sibling.exists() and sibling.is_file() and not sibling_target.exists():
                _atomic_copy_file(sibling, sibling_target)

    for p in (legacy, wal, shm):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    return True


def migrate_legacy_runtime_files(base_dir: Path | None = None) -> dict[str, bool]:
    """Migrate legacy runtime files from ``<base_dir>/memory/`` to XDG paths.

    Intended strictly for explicit daemon/CLI startup, never during unit tests.
    """
    mem_dir = (base_dir or _REPO_ROOT) / "memory"
    mapping = {
        "db": (mem_dir / "kurek_memory.db", db_path()),
        "history": (mem_dir / "kurek_history.json", history_path()),
        "clipboard": (mem_dir / "clipboard_history.json", clipboard_path()),
    }
    return {
        name: migrate_legacy_file(legacy, target)
        for name, (legacy, target) in mapping.items()
    }
