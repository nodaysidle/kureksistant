"""
memory/ledger.py — append-only memory with provenance.

Rows are only ever added: triggers abort UPDATE and DELETE. Every row carries a
source, and a correction is a new row whose `supersedes` points at the one it
replaces, so history stays and the prompt sees only rows nothing has replaced.
A retraction is a kind='retract' row marked the same way; it is bookkeeping and
never reaches the prompt.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from memory.atomic_io import chmod_private

DB_PATH = Path(__file__).resolve().parent / "kurek_memory.db"

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,
    key        TEXT,
    text       TEXT NOT NULL CHECK (length(trim(text)) > 0),
    heard      TEXT,
    source     TEXT NOT NULL CHECK (length(trim(source)) > 0),
    supersedes INTEGER REFERENCES ledger(id)
);
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
"""

_ACTIVE = "SELECT * FROM ledger l WHERE NOT EXISTS (SELECT 1 FROM ledger n WHERE n.supersedes = l.id)"

PROMPT_HEADER = (
    "[LEARNED MEMORY]\n"
    "Learned from conversations, newest wins: an entry replaces the one it supersedes. "
    "[USER RULES] outrank all of it. To correct an entry, call manage_memory with "
    "replaces=<its #id>."
)

_warned_cut: set[int] = set()


class LedgerError(Exception):
    pass


def _connect(db_path: Path | None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    chmod_private(path, f"{path}-wal", f"{path}-shm")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def append(kind: str, text: str, source: str, *, key: str | None = None,
           heard: str | None = None, supersedes: int | None = None,
           db_path: Path | None = None) -> int:
    """Add a row and return its id.

    Re-saving text that is already active returns the existing id. A row with the
    same key as an active one supersedes it unless `supersedes` names another."""
    text, source = " ".join(str(text).split()), " ".join(str(source).split())
    if not text or not source:
        raise LedgerError("text and source are both required")
    key = " ".join(key.split()).casefold() if key and key.strip() else None

    with _lock:
        conn = _connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            dup = conn.execute(f"SELECT id FROM ({_ACTIVE}) WHERE text = ?", (text,)).fetchone()
            if dup:
                conn.execute("COMMIT")
                return dup["id"]
            if supersedes is not None:
                if not conn.execute("SELECT 1 FROM ledger WHERE id = ?", (supersedes,)).fetchone():
                    raise LedgerError(f"no memory #{supersedes} to replace")
            elif key:
                prev = conn.execute(f"SELECT id FROM ({_ACTIVE}) WHERE key = ? ORDER BY id DESC",
                                    (key,)).fetchone()
                supersedes = prev["id"] if prev else None
            cur = conn.execute(
                "INSERT INTO ledger (ts, kind, key, text, heard, source, supersedes) VALUES (?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), kind, key, text, heard, source, supersedes),
            )
            conn.execute("COMMIT")
            return cur.lastrowid
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def retract(target_id: int, reason: str, source: str, *, db_path: Path | None = None) -> int:
    """Withdraw memory #target_id, keeping the row, and return the retraction's id.

    History stays: the target is superseded by a kind='retract' row that records
    why. The target leaves the prompt block; `active()` still lists both."""
    reason, source = " ".join(str(reason).split()), " ".join(str(source).split())
    if not reason or not source:
        raise LedgerError("a retraction needs a reason and a source")

    with _lock:
        conn = _connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM ledger WHERE id = ?", (target_id,)).fetchone():
                raise LedgerError(f"no memory #{target_id} to forget")
            if conn.execute("SELECT 1 FROM ledger WHERE supersedes = ?",
                            (target_id,)).fetchone():
                raise LedgerError(f"memory #{target_id} was already replaced or forgotten")
            cur = conn.execute(
                "INSERT INTO ledger (ts, kind, key, text, heard, source, supersedes) VALUES (?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), "retract", None, reason, None,
                 source, int(target_id)),
            )
            conn.execute("COMMIT")
            return cur.lastrowid
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def active(db_path: Path | None = None) -> list[sqlite3.Row]:
    """Rows nothing has replaced, oldest first."""
    with _lock:
        conn = _connect(db_path)
        try:
            return conn.execute(f"{_ACTIVE} ORDER BY id").fetchall()
        finally:
            conn.close()


# Sources Kurek writes during a chat. A correction from any other source was made outside it.
_CHAT_SOURCES = ("manage_memory", "daily/", "jev")


def outside_correction_ts(db_path: Path | None = None) -> str:
    """When memory was last corrected outside the chat (by hand or by another agent), or "".

    Chat turns from before then may still repeat what was corrected, and the model
    believes the chat over the memory block, so those turns stop going to the model."""
    with _lock:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                "SELECT ts, source FROM ledger WHERE supersedes IS NOT NULL ORDER BY id DESC"
            ).fetchall()
        finally:
            conn.close()
    return next((r["ts"] for r in rows if not r["source"].startswith(_CHAT_SOURCES)), "")


def prompt_block(limit: int, db_path: Path | None = None) -> str:
    """The learned-memory section of the system prompt, or "" when empty.

    Over `limit` the oldest rows are left out, with a warning once per row count."""
    rows = [r for r in active(db_path) if r["kind"] != "retract"]
    if not rows:
        return ""
    lines = [f"- [#{r['id']}] ({r['kind']}, {r['ts'][:10]}) {r['text']}" for r in rows]
    kept = len(lines)
    while kept > 1 and len(PROMPT_HEADER) + sum(len(ln) + 1 for ln in lines[-kept:]) > limit:
        kept -= 1
    if kept < len(lines) and len(lines) not in _warned_cut:
        _warned_cut.add(len(lines))
        print(f"[Kurek Memory] WARNING: ledger has {len(lines)} active entries; only the newest "
              f"{kept} fit in {limit} chars. Raise KUREK_MEMORY_FILE_MAX_CHARS.", flush=True)
    return PROMPT_HEADER + "\n" + "\n".join(lines[-kept:])
