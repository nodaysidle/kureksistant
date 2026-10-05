"""
actions/clipboard_tool.py — Persistent Wayland/macOS clipboard manager with pinning & voice access.
Auto-discovered by core/action_loader.py.

Capabilities:
  • 'get_latest': Reads the most recent clipboard entry.
  • 'pin': Pins the current clipboard or a specific snippet with an optional title.
  • 'unpin': Unpins a snippet.
  • 'list_pinned': Returns all pinned snippets for easy recall.
  • 'list_recent': Returns recent clipboard history.
  • 'copy' / 'restore': Copies a snippet back to the system clipboard (wl-copy / pbcopy).
  • 'search': Searches clipboard history and pinned items.
  • 'clear_unpinned': Clears unpinned history while preserving pinned clips.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = _get_base_dir()
CLIPBOARD_FILE = BASE_DIR / "memory" / "clipboard_history.json"
MAX_UNPINNED_HISTORY = 100

_STORE_LOCK = threading.Lock()
_WATCHER_THREAD: threading.Thread | None = None
_WATCHER_RUNNING = False
_LAST_SEEN_CLIP: str = ""


class ClipboardStore:
    def __init__(self, filepath: Path = CLIPBOARD_FILE):
        self.filepath = filepath
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.items: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
            except Exception:
                pass
        return []

    def _save(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.items, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Clipboard] Error saving store: {e}", flush=True)

    def add_clip(self, text: str, pinned: bool = False, title: str | None = None) -> dict:
        text = text.strip()
        if not text:
            return {}

        with _STORE_LOCK:
            # Check if this exact text is already the top item
            if self.items and self.items[0].get("text") == text:
                if pinned and not self.items[0].get("pinned"):
                    self.items[0]["pinned"] = True
                    if title:
                        self.items[0]["title"] = title
                    self._save()
                return self.items[0]

            new_id = (max([item.get("id", 0) for item in self.items], default=0)) + 1
            clip_entry = {
                "id": new_id,
                "text": text,
                "preview": text[:80] + ("..." if len(text) > 80 else ""),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "pinned": bool(pinned),
                "title": title or None,
            }

            # Insert at the beginning (most recent first)
            self.items.insert(0, clip_entry)

            # Prune unpinned items exceeding limit
            unpinned = [it for it in self.items if not it.get("pinned")]
            if len(unpinned) > MAX_UNPINNED_HISTORY:
                to_remove_ids = {it["id"] for it in unpinned[MAX_UNPINNED_HISTORY:]}
                self.items = [it for it in self.items if it["id"] not in to_remove_ids]

            self._save()
            return clip_entry

    def get_latest(self) -> dict | None:
        with _STORE_LOCK:
            return self.items[0] if self.items else None

    def pin_latest(self, title: str | None = None) -> dict | None:
        with _STORE_LOCK:
            if not self.items:
                return None
            self.items[0]["pinned"] = True
            if title:
                self.items[0]["title"] = title
            self._save()
            return self.items[0]

    def pin_by_identifier(self, identifier: str | int, title: str | None = None) -> dict | None:
        with _STORE_LOCK:
            # Try by integer ID
            try:
                item_id = int(identifier)
                for it in self.items:
                    if it.get("id") == item_id:
                        it["pinned"] = True
                        if title:
                            it["title"] = title
                        self._save()
                        return it
            except ValueError:
                pass

            # Try by matching title or text substring
            query = str(identifier).lower().strip()
            for it in self.items:
                if (it.get("title") and query in it["title"].lower()) or query in it.get("text", "").lower():
                    it["pinned"] = True
                    if title:
                        it["title"] = title
                    self._save()
                    return it

        return None

    def unpin(self, identifier: str | int) -> bool:
        with _STORE_LOCK:
            try:
                item_id = int(identifier)
                for it in self.items:
                    if it.get("id") == item_id:
                        it["pinned"] = False
                        self._save()
                        return True
            except ValueError:
                pass

            query = str(identifier).lower().strip()
            for it in self.items:
                if (it.get("title") and query in it["title"].lower()) or query in it.get("text", "").lower():
                    it["pinned"] = False
                    self._save()
                    return True
        return False

    def list_pinned(self) -> list[dict]:
        with _STORE_LOCK:
            return [it for it in self.items if it.get("pinned")]

    def list_recent(self, limit: int = 10) -> list[dict]:
        with _STORE_LOCK:
            return self.items[:limit]

    def search(self, query: str) -> list[dict]:
        q = query.lower().strip()
        with _STORE_LOCK:
            results = []
            for it in self.items:
                if (it.get("title") and q in it["title"].lower()) or (q in it.get("text", "").lower()):
                    results.append(it)
            return results

    def clear_unpinned(self) -> int:
        with _STORE_LOCK:
            orig_len = len(self.items)
            self.items = [it for it in self.items if it.get("pinned")]
            cleared = orig_len - len(self.items)
            self._save()
            return cleared


_GLOBAL_STORE = ClipboardStore()


def _read_system_clipboard() -> str:
    """Reads system clipboard text safely without blocking."""
    if sys.platform == "darwin":
        try:
            res = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=1.0)
            return res.stdout if res.returncode == 0 else ""
        except Exception:
            return ""
    elif shutil.which("wl-paste"):
        try:
            res = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, text=True, timeout=1.0)
            return res.stdout if res.returncode == 0 else ""
        except Exception:
            return ""
    elif shutil.which("xclip"):
        try:
            res = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, timeout=1.0)
            return res.stdout if res.returncode == 0 else ""
        except Exception:
            return ""
    return ""


def _write_system_clipboard(text: str) -> bool:
    """Writes text back to system clipboard."""
    if sys.platform == "darwin":
        try:
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"), timeout=1.0)
            return p.returncode == 0
        except Exception:
            return False
    elif shutil.which("wl-copy"):
        try:
            p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"), timeout=1.0)
            return p.returncode == 0
        except Exception:
            return False
    elif shutil.which("xclip"):
        try:
            p = subprocess.Popen(["xclip", "-selection", "clipboard", "-i"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"), timeout=1.0)
            return p.returncode == 0
        except Exception:
            return False
    return False


def _clipboard_watcher_loop():
    global _WATCHER_RUNNING, _LAST_SEEN_CLIP
    # Seed with current clip
    _LAST_SEEN_CLIP = _read_system_clipboard().strip()
    if _LAST_SEEN_CLIP:
        _GLOBAL_STORE.add_clip(_LAST_SEEN_CLIP)

    while _WATCHER_RUNNING:
        try:
            current = _read_system_clipboard().strip()
            if current and current != _LAST_SEEN_CLIP:
                _LAST_SEEN_CLIP = current
                _GLOBAL_STORE.add_clip(current)
        except Exception:
            pass
        time.sleep(1.0)


def start_clipboard_watcher():
    global _WATCHER_THREAD, _WATCHER_RUNNING
    if _WATCHER_RUNNING:
        return
    _WATCHER_RUNNING = True
    _WATCHER_THREAD = threading.Thread(target=_clipboard_watcher_loop, daemon=True, name="ClipboardWatcher")
    _WATCHER_THREAD.start()
    print("[Kurek Clipboard] Background watcher thread started.", flush=True)


# Auto-start watcher on module import
start_clipboard_watcher()


def manage_clipboard(parameters: dict, player=None, session_memory=None) -> str:
    """Primary tool handler for clipboard operations."""
    params = parameters or {}
    action = params.get("action", "get_latest").lower().strip()
    title = params.get("title")
    query = params.get("query") or params.get("text") or params.get("identifier") or ""

    if action in ("get_latest", "read", "current", "latest"):
        clip = _GLOBAL_STORE.get_latest()
        if not clip:
            # Fall back to live read
            live = _read_system_clipboard().strip()
            if live:
                _GLOBAL_STORE.add_clip(live)
                return f"Clipboard content: {live}"
            return "Your clipboard is currently empty."

        status = " (pinned)" if clip.get("pinned") else ""
        title_str = f" [{clip['title']}]" if clip.get("title") else ""
        return f"Latest clipboard{title_str}{status}: {clip['text']}"

    elif action in ("pin", "pin_latest", "save_pin"):
        if query:
            pinned_item = _GLOBAL_STORE.pin_by_identifier(query, title=title)
        else:
            pinned_item = _GLOBAL_STORE.pin_latest(title=title)

        if pinned_item:
            label = f"'{pinned_item['title']}'" if pinned_item.get("title") else f"Clip #{pinned_item['id']}"
            return f"Successfully pinned {label}: {pinned_item['preview']}"
        return "Could not find a clipboard entry to pin."

    elif action in ("unpin", "remove_pin"):
        if not query:
            return "Please specify which pinned item to unpin (by title, ID, or snippet text)."
        if _GLOBAL_STORE.unpin(query):
            return f"Successfully unpinned clip matching: {query}"
        return f"No pinned clip found matching: {query}"

    elif action in ("list_pinned", "pins", "pinned"):
        pins = _GLOBAL_STORE.list_pinned()
        if not pins:
            return "You do not have any pinned snippets yet. You can say 'pin my clipboard as [title]' to pin any snippet."

        lines = ["Pinned clips:"]
        for p in pins:
            title_part = f"[{p['title']}] " if p.get("title") else ""
            lines.append(f"• #{p['id']} {title_part}{p['preview']}")
        return "\n".join(lines)

    elif action in ("list_recent", "history", "recent"):
        recent = _GLOBAL_STORE.list_recent(limit=10)
        if not recent:
            return "Clipboard history is empty."
        lines = ["Recent clipboard history:"]
        for r in recent:
            pin_badge = "📌 " if r.get("pinned") else ""
            title_part = f"[{r['title']}] " if r.get("title") else ""
            lines.append(f"• #{r['id']} {pin_badge}{title_part}{r['preview']}")
        return "\n".join(lines)

    elif action in ("copy", "restore", "set"):
        text_to_copy = ""
        # If user passed explicit text to copy
        if params.get("text"):
            text_to_copy = params["text"]
        elif query:
            # Search by ID, title, or query
            matches = _GLOBAL_STORE.search(query)
            if matches:
                # Prefer pinned match first, then most recent
                pinned_matches = [m for m in matches if m.get("pinned")]
                match = pinned_matches[0] if pinned_matches else matches[0]
                text_to_copy = match["text"]
            else:
                text_to_copy = query

        if not text_to_copy:
            return "No text or matching snippet found to copy."

        if _write_system_clipboard(text_to_copy):
            _GLOBAL_STORE.add_clip(text_to_copy, title=title)
            preview = text_to_copy[:60] + ("..." if len(text_to_copy) > 60 else "")
            return f"Copied to system clipboard: '{preview}'. You can now paste it anywhere."
        return "Failed to write to system clipboard."

    elif action in ("search", "find"):
        if not query:
            return "Please provide a query to search your clipboard history."
        results = _GLOBAL_STORE.search(query)
        if not results:
            return f"No clipboard items found matching '{query}'."
        lines = [f"Found {len(results)} matches for '{query}':"]
        for r in results[:5]:
            pin_badge = "📌 " if r.get("pinned") else ""
            title_part = f"[{r['title']}] " if r.get("title") else ""
            lines.append(f"• #{r['id']} {pin_badge}{title_part}{r['preview']}")
        return "\n".join(lines)

    elif action in ("clear_unpinned", "clean"):
        cleared = _GLOBAL_STORE.clear_unpinned()
        return f"Cleared {cleared} unpinned history items. All pinned clips remain safely stored."

    return (
        f"Unknown clipboard action '{action}'. "
        "Available actions: get_latest, pin, unpin, list_pinned, list_recent, copy, search, clear_unpinned."
    )


TOOL = {
    "name": "manage_clipboard",
    "description": (
        "Wayland/macOS clipboard manager with persistent storage and pinning. "
        "Use 'get_latest' to read what the user recently copied. "
        "Use 'pin' to bookmark the current clipboard or a snippet with a title (e.g. 'Stripe API key', 'Docker command'). "
        "Use 'list_pinned' to view all pinned snippets. "
        "Use 'copy' to restore a pinned snippet or text back into the system clipboard so the user can paste it. "
        "Use 'search' to find previously copied text or API tokens."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "get_latest | pin | unpin | list_pinned | list_recent | copy | search | clear_unpinned"
            },
            "title": {
                "type": "STRING",
                "description": "Optional title or label for pinned snippets (e.g. 'Supabase Key', 'Regex Pattern')."
            },
            "query": {
                "type": "STRING",
                "description": "Search keyword, clip ID, title, or snippet identifier to find/restore/pin."
            },
            "text": {
                "type": "STRING",
                "description": "Specific text content to write into the clipboard if action is 'copy'."
            }
        },
        "required": ["action"]
    },
    "handler": manage_clipboard
}
