import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from threading import Lock


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
MEMORY_DIR = BASE_DIR / "memory"
MEMORY_PATH = MEMORY_DIR / "long_term.json"
DB_PATH = MEMORY_DIR / "kurek_memory.db"
_lock = Lock()
_log = logging.getLogger("kurek.memory")

MAX_VALUE_LENGTH = 380
MEMORY_MAX_CHARS = 200_000
PROMPT_CORE_CHARS = 900
PROMPT_INDEX_CHARS = 420
PROMPT_MAX_PER_CATEGORY = 6

_trim_notifier = None


def set_trim_notifier(fn) -> None:
    """Register a callable(str) that surfaces trims to the user."""
    global _trim_notifier
    _trim_notifier = fn


def _empty_memory() -> dict:
    return {
        "identity": {},
        "preferences": {},
        "projects": {},
        "relationships": {},
        "wishes": {},
        "notes": {},
    }


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            category TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated TEXT NOT NULL,
            PRIMARY KEY (category, key)
        );
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
            category,
            key,
            value,
            content='entries',
            content_rowid='rowid'
        );
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
            INSERT INTO entries_fts(rowid, category, key, value)
            VALUES (new.rowid, new.category, new.key, new.value);
        END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts, rowid, category, key, value)
            VALUES('delete', old.rowid, old.category, old.key, old.value);
        END;
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts, rowid, category, key, value)
            VALUES('delete', old.rowid, old.category, old.key, old.value);
            INSERT INTO entries_fts(rowid, category, key, value)
            VALUES (new.rowid, new.category, new.key, new.value);
        END;
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            summary TEXT NOT NULL,
            language TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    return conn


def _migrate_from_json_if_needed(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("SELECT value FROM metadata WHERE key='migrated'")
    row = cur.fetchone()
    if row and row["value"] == "1":
        return

    if MEMORY_PATH.exists():
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for cat, items in data.items():
                    if cat == "sessions" and isinstance(items, list):
                        for s in items:
                            if isinstance(s, dict):
                                cur.execute(
                                    "INSERT INTO sessions (date, summary, language) VALUES (?, ?, ?)",
                                    (
                                        s.get("date", datetime.now().strftime("%Y-%m-%d")),
                                        s.get("summary", ""),
                                        s.get("language", ""),
                                    ),
                                )
                    elif cat == "monitors" and isinstance(items, dict):
                        cur.execute(
                            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('monitors', ?)",
                            (json.dumps(items),),
                        )
                    elif isinstance(items, dict):
                        for k, v in items.items():
                            val = v.get("value", "") if isinstance(v, dict) else str(v)
                            upd = (
                                v.get("updated", datetime.now().strftime("%Y-%m-%d"))
                                if isinstance(v, dict)
                                else datetime.now().strftime("%Y-%m-%d")
                            )
                            if val:
                                cur.execute(
                                    "INSERT OR REPLACE INTO entries (category, key, value, updated) VALUES (?, ?, ?, ?)",
                                    (cat, k, val, upd),
                                )
                cur.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated', '1')")
                conn.commit()
                print("[Memory] 📦 Migrated legacy long_term.json into kurek_memory.db (WAL + FTS5)")
        except Exception as e:
            # Leave 'migrated' unset so the next start retries, and discard any
            # half-written rows so a retry cannot duplicate sessions.
            conn.rollback()
            _log.exception("Migration from %s failed; will retry on next start", MEMORY_PATH)
            print(f"[Memory] Migration error (will retry next start): {e}")
    else:
        cur.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated', '1')")
        conn.commit()


def _is_migrated() -> bool:
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
        try:
            row = conn.execute("SELECT value FROM metadata WHERE key='migrated'").fetchone()
            return bool(row and row[0] == "1")
        finally:
            conn.close()
    except Exception:
        return False


def _sync_to_legacy_json(memory_dict: dict) -> None:
    """Keep long_term.json in sync as a compatibility mirror.

    Never overwrite the legacy file before its contents were migrated, or a
    failed migration would destroy the only copy of the user's memory."""
    if MEMORY_PATH.exists() and not _is_migrated():
        _log.warning("Skipping long_term.json mirror: migration not complete")
        return
    try:
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_PATH.write_text(
            json.dumps(memory_dict, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def load_memory() -> dict:
    with _lock:
        conn = _get_db()
        try:
            _migrate_from_json_if_needed(conn)
            data = _empty_memory()
            cur = conn.cursor()
            cur.execute("SELECT category, key, value, updated FROM entries")
            for row in cur.fetchall():
                cat = row["category"]
                if cat not in data:
                    data[cat] = {}
                data[cat][row["key"]] = {
                    "value": row["value"],
                    "updated": row["updated"],
                }

            cur.execute("SELECT date, summary, language FROM sessions ORDER BY id ASC")
            sessions = []
            for row in cur.fetchall():
                s_entry = {"date": row["date"], "summary": row["summary"]}
                if row["language"]:
                    s_entry["language"] = row["language"]
                sessions.append(s_entry)
            if sessions:
                data["sessions"] = sessions

            cur.execute("SELECT value FROM metadata WHERE key='monitors'")
            mon = cur.fetchone()
            if mon and mon["value"]:
                try:
                    data["monitors"] = json.loads(mon["value"])
                except Exception:
                    pass

            return data
        finally:
            conn.close()


def load_monitors() -> dict:
    with _lock:
        conn = _get_db()
        try:
            _migrate_from_json_if_needed(conn)
            cur = conn.cursor()
            cur.execute("SELECT value FROM metadata WHERE key='monitors'")
            row = cur.fetchone()
            if row and row["value"]:
                try:
                    return json.loads(row["value"])
                except Exception:
                    pass
            return {}
        finally:
            conn.close()


def save_monitors(monitors: dict) -> None:
    with _lock:
        conn = _get_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('monitors', ?)",
                (json.dumps(monitors),),
            )
            conn.commit()
        finally:
            conn.close()
    # Mirror update to legacy JSON
    mem = load_memory()
    mem["monitors"] = monitors
    _sync_to_legacy_json(mem)


def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((cat, key, entry))
    return entries


def _trim_to_limit(memory: dict) -> dict:
    if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
        return memory
    entries = _all_entries(memory)
    entries.sort(key=lambda t: t[2].get("updated", "0000-00-00"))
    dropped = []
    for cat, key, _ in entries:
        if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
            break
        del memory[cat][key]
        dropped.append(f"{cat}/{key}")
        print(f"[Memory] 🗑️  Trimmed {cat}/{key}")
    if dropped and _trim_notifier:
        try:
            _trim_notifier(
                f"SYS: Memory full — forgot {len(dropped)} oldest entries "
                f"({', '.join(dropped[:3])}{'…' if len(dropped) > 3 else ''})"
            )
        except Exception:
            pass
    return memory


def save_memory(memory: dict) -> None:
    if not isinstance(memory, dict):
        return
    memory = _trim_to_limit(memory)
    with _lock:
        conn = _get_db()
        try:
            # Migrate first, so a write before any read can't skip the import.
            _migrate_from_json_if_needed(conn)
            cur = conn.cursor()
            cur.execute("DELETE FROM entries")
            for cat, items in memory.items():
                if cat in ("sessions", "monitors"):
                    continue
                if isinstance(items, dict):
                    for k, v in items.items():
                        val = v.get("value", "") if isinstance(v, dict) else str(v)
                        upd = (
                            v.get("updated", datetime.now().strftime("%Y-%m-%d"))
                            if isinstance(v, dict)
                            else datetime.now().strftime("%Y-%m-%d")
                        )
                        if val:
                            cur.execute(
                                "INSERT OR REPLACE INTO entries (category, key, value, updated) VALUES (?, ?, ?, ?)",
                                (cat, k, val, upd),
                            )

            if "sessions" in memory and isinstance(memory["sessions"], list):
                cur.execute("DELETE FROM sessions")
                for s in memory["sessions"][-3:]:
                    if isinstance(s, dict):
                        cur.execute(
                            "INSERT INTO sessions (date, summary, language) VALUES (?, ?, ?)",
                            (
                                s.get("date", datetime.now().strftime("%Y-%m-%d")),
                                s.get("summary", ""),
                                s.get("language", ""),
                            ),
                        )

            if "monitors" in memory and isinstance(memory["monitors"], dict):
                cur.execute(
                    "INSERT OR REPLACE INTO metadata (key, value) VALUES ('monitors', ?)",
                    (json.dumps(memory["monitors"]),),
                )

            conn.commit()
        finally:
            conn.close()

    _sync_to_legacy_json(memory)


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _recursive_update(target: dict, updates: dict) -> bool:
    changed = False
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
        else:
            new_val = _truncate_value(str(value["value"] if isinstance(value, dict) else value))
            entry = {"value": new_val, "updated": datetime.now().strftime("%Y-%m-%d")}
            existing = target.get(key, {})
            if not isinstance(existing, dict) or existing.get("value") != new_val:
                target[key] = entry
                changed = True
    return changed


def update_memory(memory_update: dict) -> dict:
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()
    memory = load_memory()
    if _recursive_update(memory, memory_update):
        save_memory(memory)
        print(f"[Memory] 💾 Saved: {list(memory_update.keys())}")
    return memory


def _entry_value(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("value", "") or "").strip()
    return str(entry or "").strip()


def _pretty(key: str) -> str:
    return key.replace("_", " ").strip()


_CATEGORY_LABELS = {
    "preferences": "Preferences",
    "projects": "Active projects / goals",
    "relationships": "People in their life",
    "wishes": "Wishes / plans",
    "notes": "Notes",
}

_IDENTITY_FIELDS = [
    "name", "age", "birthday", "city", "job",
    "language", "school", "nationality",
]


def format_memory_for_prompt(memory: dict | None) -> str:
    if not memory:
        return ""

    core_lines: list[str] = []

    # 1. Identity - always, in full
    identity = memory.get("identity", {}) or {}
    for field in _IDENTITY_FIELDS:
        val = _entry_value(identity.get(field))
        if not val:
            continue
        if field == "language":
            core_lines.append(
                f"Has spoken to you in: {val} (an observation about the past — "
                f"always answer in the language of their CURRENT message)"
            )
        else:
            core_lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in _IDENTITY_FIELDS:
            continue
        val = _entry_value(entry)
        if val:
            core_lines.append(f"{_pretty(key).title()}: {val}")

    # 2. Everything else, most recently updated first
    rest: list[tuple[str, str, str, str]] = []
    for cat in _CATEGORY_LABELS:
        for key, entry in (memory.get(cat, {}) or {}).items():
            val = _entry_value(entry)
            if not val:
                continue
            updated = (entry.get("updated", "") if isinstance(entry, dict) else "") or "0000-00-00"
            rest.append((updated, cat, key, val))
    rest.sort(key=lambda t: t[0], reverse=True)

    used = sum(len(l) + 1 for l in core_lines)
    shown: dict[str, list[str]] = {}
    overflow: dict[str, list[str]] = {}

    for updated, cat, key, val in rest:
        shown_in_cat = shown.get(cat, [])
        if len(shown_in_cat) >= PROMPT_MAX_PER_CATEGORY:
            overflow.setdefault(cat, []).append(_pretty(key))
            continue

        rendered = f"- {_pretty(key)}: {val}"
        cost = len(rendered) + 1
        if used + cost <= PROMPT_CORE_CHARS:
            shown.setdefault(cat, []).append(rendered)
            used += cost
        else:
            overflow.setdefault(cat, []).append(_pretty(key))

    for cat, label in _CATEGORY_LABELS.items():
        items = shown.get(cat)
        if items:
            core_lines.append(f"\n{label}:")
            core_lines.extend(items)

    out = ["[USER PERSONAL CONTEXT & IDENTITY]"]
    out.extend(core_lines)

    index_lines: list[str] = []
    index_used = 0
    for cat, label in _CATEGORY_LABELS.items():
        keys = overflow.get(cat)
        if not keys:
            continue
        rendered_keys = ", ".join(keys)
        line = f"- {label}: {rendered_keys}"
        if index_used + len(line) + 1 <= PROMPT_INDEX_CHARS:
            index_lines.append(line)
            index_used += len(line) + 1
        else:
            break

    if index_lines:
        out.append(
            "\n[ALSO REMEMBERED - NOT SHOWN TO SAVE SPACE]\n"
            "You know about the following topics. If the user asks about ANY of them, "
            "or about anything personal you cannot see above, CALL manage_memory(action='recall', query=...) "
            "BEFORE saying you don't know:"
        )
        out.extend(index_lines)

    return "\n".join(out).strip()


def _score(query_words: list[str], cat: str, key: str, value: str) -> int:
    hay_key = _pretty(key).lower()
    hay_val = value.lower()
    score = 0
    for w in query_words:
        if not w:
            continue
        if w == hay_key:
            score += 10
        elif w in hay_key:
            score += 6
        if w in hay_val:
            score += 3
        if w in cat:
            score += 1
    return score


def search_memory(query: str, limit: int = 8) -> str:
    """Find stored facts matching `query` using SQLite FTS5 index with lexical fallback."""
    words = [w for w in re.split(r"[^\w]+", (query or "").lower()) if len(w) > 1]

    # Try FTS5 fast search if query words provided
    with _lock:
        conn = _get_db()
        try:
            _migrate_from_json_if_needed(conn)
            cur = conn.cursor()
            rows: list[tuple[int, str, str, str]] = []
            if words:
                fts_query = " OR ".join(f'"{w}"*' for w in words)
                try:
                    cur.execute(
                        """
                        SELECT category, key, value, rank
                        FROM entries_fts
                        WHERE entries_fts MATCH ?
                        ORDER BY rank
                        LIMIT ?;
                        """,
                        (fts_query, max(limit * 2, 16)),
                    )
                    for r in cur.fetchall():
                        cat, k, v = r["category"], r["key"], r["value"]
                        s = _score(words, cat, k, v)
                        rows.append((s if s > 0 else 1, cat, k, v))
                except Exception:
                    pass

            if not rows:
                # Fallback to in-memory score scan
                cur.execute("SELECT category, key, value FROM entries")
                for r in cur.fetchall():
                    cat, k, v = r["category"], r["key"], r["value"]
                    s = _score(words, cat, k, v) if words else 1
                    if s > 0:
                        rows.append((s, cat, k, v))

            if not rows:
                return (
                    f"Nothing stored about '{query}'."
                    if query
                    else "I have not stored anything about this person yet."
                )

            rows.sort(key=lambda r: (-r[0], r[2]))
            lines = [f"{cat}/{_pretty(key)}: {val}" for _s, cat, key, val in rows[:max(1, limit)]]
            head = (
                f"Stored facts matching '{query}':"
                if query
                else "Everything currently stored:"
            )
            more = (
                f"\n(+{len(rows) - len(lines)} more — search with a narrower keyword)"
                if len(rows) > len(lines)
                else ""
            )
            return head + "\n" + "\n".join(lines) + more
        finally:
            conn.close()


def all_entries_for_ui() -> list[dict]:
    with _lock:
        conn = _get_db()
        try:
            _migrate_from_json_if_needed(conn)
            cur = conn.cursor()
            cur.execute("SELECT category, key, value, updated FROM entries ORDER BY updated DESC")
            return [
                {
                    "category": r["category"],
                    "key": r["key"],
                    "value": r["value"],
                    "updated": r["updated"],
                }
                for r in cur.fetchall()
            ]
        finally:
            conn.close()


def remember(key: str, value: str, category: str = "notes") -> str:
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    with _lock:
        conn = _get_db()
        try:
            _migrate_from_json_if_needed(conn)
            cur = conn.cursor()
            cur.execute("DELETE FROM entries WHERE category=? AND key=?", (category, key))
            deleted = cur.rowcount > 0
            conn.commit()
        finally:
            conn.close()

    if deleted:
        _sync_to_legacy_json(load_memory())
        return f"Forgotten: {category}/{key}"
    return f"Not found: {category}/{key}"


forget_memory = forget

_SESSION_MAX = 3


def save_session_summary(summary: str, language: str = "") -> None:
    summary = (summary or "").strip()
    if not summary:
        return
    with _lock:
        conn = _get_db()
        try:
            _migrate_from_json_if_needed(conn)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO sessions (date, summary, language) VALUES (?, ?, ?)",
                (datetime.now().strftime("%Y-%m-%d"), summary[:280], language),
            )
            cur.execute(
                """
                DELETE FROM sessions WHERE id NOT IN (
                    SELECT id FROM sessions ORDER BY id DESC LIMIT ?
                )
                """,
                (_SESSION_MAX,),
            )
            conn.commit()
        finally:
            conn.close()

    mem = load_memory()
    _sync_to_legacy_json(mem)
    print(f"[Memory] 📝 Session saved ({datetime.now().strftime('%Y-%m-%d')}): {summary[:60]}…")


def pop_last_session() -> dict | None:
    with _lock:
        conn = _get_db()
        try:
            _migrate_from_json_if_needed(conn)
            cur = conn.cursor()
            cur.execute("SELECT id, date, summary, language FROM sessions ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if not row:
                return None
            sess_id = row["id"]
            entry = {"date": row["date"], "summary": row["summary"]}
            if row["language"]:
                entry["language"] = row["language"]
            cur.execute("DELETE FROM sessions WHERE id=?", (sess_id,))
            conn.commit()
        finally:
            conn.close()
    # Keep the legacy long_term.json mirror in step, so nothing reading it can
    # replay a session that was already consumed. (No-op until migrated.)
    _sync_to_legacy_json(load_memory())
    return entry
