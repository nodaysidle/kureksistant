"""
memory/recall_index.py — an FTS5 index of the daily logs, for recall.

The logs in ~/memory/YYYY-MM-DD.md stay the source of truth; this index is a
disposable copy in the ledger's database file, refreshed from each file's
mtime and size. It never touches the ledger table, and the day and text it
returns are what was said, not what is true.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from memory import ledger
from memory.atomic_io import chmod_private

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_files (
    file  TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size  INTEGER NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS daily_fts USING fts5(
    day UNINDEXED,
    text,
    tokenize='porter unicode61'
);
"""


def _connect(db_path: Path | None) -> sqlite3.Connection:
    path = db_path or ledger.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    chmod_private(path, f"{path}-wal", f"{path}-shm")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _logs() -> list[Path]:
    return sorted((Path.home() / "memory").glob("????-??-??.md"))


def _turns(log: Path) -> list[str]:
    """The collapsed turns of one daily log; the header chunk before the first ### is not a turn."""
    try:
        chunks = log.read_text(encoding="utf-8").split("\n### ")[1:]
    except OSError:
        return []
    return [text for text in (" ".join(c.split()) for c in chunks) if text]


def _refresh(conn: sqlite3.Connection) -> None:
    known = {r["file"]: (r["mtime"], r["size"]) for r in conn.execute("SELECT * FROM daily_files")}
    seen: set[str] = set()
    for log in _logs():
        name = str(log)
        seen.add(name)
        try:
            stat = log.stat()
        except OSError:
            continue
        if known.get(name) == (stat.st_mtime, stat.st_size):
            continue
        conn.execute("DELETE FROM daily_fts WHERE day = ?", (log.stem,))
        conn.executemany("INSERT INTO daily_fts (day, text) VALUES (?, ?)",
                         [(log.stem, turn) for turn in _turns(log)])
        conn.execute("INSERT INTO daily_files (file, mtime, size) VALUES (?,?,?) "
                     "ON CONFLICT(file) DO UPDATE SET mtime=excluded.mtime, size=excluded.size",
                     (name, stat.st_mtime, stat.st_size))
    for name in set(known) - seen:
        conn.execute("DELETE FROM daily_fts WHERE day = ?", (Path(name).stem,))
        conn.execute("DELETE FROM daily_files WHERE file = ?", (name,))


def _query(words: set[str]) -> str:
    """OR of the words, each quoted as an FTS string so user text is never FTS syntax."""
    return " OR ".join('"' + w.replace('"', '""') + '"' for w in sorted(words))


def search(words: set[str], limit: int = 5, days: list[str] | None = None) -> list[tuple[str, str]]:
    """(day, text) for past turns matching any keyword, best match then newest day first.

    With `days`, only turns from those YYYY-MM-DD days are searched."""
    if not words:
        return []
    day_sql, day_args = _day_filter(days)
    return _run(
        "SELECT day, text FROM daily_fts WHERE daily_fts MATCH ?" + day_sql +
        " ORDER BY bm25(daily_fts), day DESC LIMIT ?",
        (_query(words), *day_args, int(limit)),
    )


def recent(days: list[str], limit: int = 5) -> list[tuple[str, str]]:
    """(day, text) for the last turns of the given days, newest first."""
    if not days:
        return []
    day_sql, day_args = _day_filter(days)
    return _run(
        "SELECT day, text FROM daily_fts WHERE 1" + day_sql + " ORDER BY day DESC, rowid DESC LIMIT ?",
        (*day_args, int(limit)),
    )


def _day_filter(days: list[str] | None) -> tuple[str, tuple]:
    if not days:
        return "", ()
    return f" AND day IN ({','.join('?' * len(days))})", tuple(days)


def _run(sql: str, args: tuple) -> list[tuple[str, str]]:
    with _lock:
        conn = _connect(None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            _refresh(conn)
            rows = conn.execute(sql, args).fetchall()
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    return [(r["day"], r["text"]) for r in rows]
