"""
actions/vault_knowledge.py — NODAYSIDLE Knowledge Vault & LLM-Wiki Tool for Kurek.
Auto-discovered by core/action_loader.py.

Capabilities:
  • 'list_projects': Instant structured overview of active portfolio projects, releases, and status.
  • 'read': Reads specific project concept notes, PRDs, architectural overviews, or social media / X promo strategies.
  • 'search': Fast keyword search across all notes in the vault.
  • 'add_inbox': Captures an idea, task, or note into vault inbox (YYYY-MM-DD-slug.md).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


def _resolve_vault_dir() -> Path | None:
    # 1. Environment variables
    for env_k in ("KUREK_VAULT_DIR", "NODAYSIDLE_VAULT_DIR", "VAULT_DIR"):
        val = os.environ.get(env_k)
        if val and Path(val).exists() and Path(val).is_dir():
            return Path(val).resolve()

    # 2. Config file
    cfg_path = Path.home() / ".config" / "kurek" / "vault_path"
    if cfg_path.exists():
        try:
            val = cfg_path.read_text(encoding="utf-8").strip()
            if val and Path(val).exists() and Path(val).is_dir():
                return Path(val).resolve()
        except Exception:
            pass

    # 3. Expanduser fallbacks (no hardcoded absolute home paths)
    candidates = [
        Path.home() / "dev" / "nodaysidle" / "nodaysidle-knowledge",
        Path.home() / "Projects" / "nodaysidle-knowledge",
    ]
    for cand in candidates:
        if cand.exists() and cand.is_dir():
            return cand.resolve()

    return None


def _path_within_vault(vault_dir: Path, path: Path) -> bool:
    """True if resolved path stays inside vault_dir (blocks symlink escape)."""
    vault_resolved = vault_dir.resolve()
    try:
        path.resolve().relative_to(vault_resolved)
        return True
    except ValueError:
        return False


def _clean_markdown(text: str) -> str:
    """Strip YAML frontmatter metadata and extraneous whitespace to conserve LLM tokens."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2]
    return text.strip()


def vault_knowledge(parameters: dict | None = None, **kwargs) -> str:
    params = parameters or {}
    action = (params.get("action") or "list_projects").lower().strip()
    query = (params.get("query") or "").strip()
    note_name = (params.get("note_name") or "").strip()
    content = (params.get("content") or "").strip()
    title = (params.get("title") or "").strip()

    vault_dir = _resolve_vault_dir()
    if not vault_dir:
        return (
            "Error: nodaysidle-knowledge vault directory not found. "
            "Please ensure ~/dev/nodaysidle/nodaysidle-knowledge exists or set KUREK_VAULT_DIR."
        )

    # -------------------------------------------------------------
    # Action: list_projects
    # -------------------------------------------------------------
    if action in ("list_projects", "projects", "status"):
        moc_file = vault_dir / "wiki" / "MOC" / "moc-nodaysidle-knowledge.md"
        if not moc_file.exists():
            return f"Vault found at {vault_dir}, but MOC index wiki/MOC/moc-nodaysidle-knowledge.md is missing."

        raw = moc_file.read_text(encoding="utf-8")
        clean = _clean_markdown(raw)
        return f"[NODAYSIDLE PORTFOLIO OVERVIEW from {vault_dir.name}]\n{clean}"

    # -------------------------------------------------------------
    # Action: read
    # -------------------------------------------------------------
    elif action == "read":
        if not note_name:
            return "Error: note_name parameter is required when action='read' (e.g. 'kureksistant-overview' or 'x-promo-strategy')."

        note_path = Path(note_name)
        if note_path.is_absolute() or ".." in note_path.parts:
            return (
                "Error: note_name must be a relative path within the vault; "
                "absolute paths and '..' traversal are not allowed."
            )

        slug = note_name.lower().replace(".md", "").strip()
        found_file: Path | None = None

        # 1. Exact relative path
        candidate = vault_dir / f"{note_name}.md" if not note_name.endswith(".md") else vault_dir / note_name
        if candidate.exists() and candidate.is_file() and _path_within_vault(vault_dir, candidate):
            found_file = candidate

        # 2. Search common subfolders
        if not found_file:
            search_folders = ["wiki/concepts", "wiki/MOC", "briefs", "artifacts", "sources/index", "inbox", "sessions"]
            for s_folder in search_folders:
                c = vault_dir / s_folder / f"{slug}.md"
                if c.exists() and c.is_file() and _path_within_vault(vault_dir, c):
                    found_file = c
                    break

        # 3. Fuzzy search for filename across vault
        if not found_file:
            for p in vault_dir.rglob("*.md"):
                if p.name.startswith("."):
                    continue
                if slug in p.stem.lower() and _path_within_vault(vault_dir, p):
                    found_file = p
                    break

        if not found_file:
            # Suggest matching notes
            matches = [
                p.stem for p in vault_dir.rglob("*.md")
                if not p.name.startswith(".") and any(w in p.stem.lower() for w in slug.split("-"))
            ][:5]
            suggestion = f" Matching candidates: {', '.join(matches)}" if matches else ""
            return f"Note '{note_name}' not found in {vault_dir.name}.{suggestion}"

        if not _path_within_vault(vault_dir, found_file):
            return (
                "Error: resolved note path is outside the vault directory; "
                "refusing to read."
            )

        vault_resolved = vault_dir.resolve()
        found_resolved = found_file.resolve()
        text = found_resolved.read_text(encoding="utf-8")
        cleaned = _clean_markdown(text)
        rel_path = found_resolved.relative_to(vault_resolved)

        # Cap length if excessively large
        if len(cleaned) > 8000:
            cleaned = cleaned[:8000] + "\n\n... [Truncated for voice/context brevity. Request specific sections if needed.]"

        return f"[VAULT NOTE: {rel_path}]\n{cleaned}"

    # -------------------------------------------------------------
    # Action: search
    # -------------------------------------------------------------
    elif action == "search":
        if not query:
            return "Error: query parameter is required when action='search'."

        results = []
        # Prefer fast ripgrep if available
        if shutil.which("rg"):
            try:
                cmd = [
                    "rg",
                    "-i",
                    "--max-count", "3",
                    "--glob", "!.*",
                    "--glob", "!_audit/*",
                    query,
                    str(vault_dir),
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=3.0)
                if res.returncode == 0 and res.stdout.strip():
                    lines = res.stdout.strip().splitlines()[:15]
                    formatted_matches = []
                    for line in lines:
                        if ":" in line:
                            path_str, text_str = line.split(":", 1)
                            rel = Path(path_str).relative_to(vault_dir)
                            formatted_matches.append(f"• [{rel}]: {text_str.strip()[:140]}")
                    if formatted_matches:
                        return f"[VAULT SEARCH: '{query}'] Found {len(formatted_matches)} occurrences:\n" + "\n".join(formatted_matches)
            except Exception:
                pass

        # Python fallback search
        words = [w.lower() for w in query.split() if len(w) > 2]
        # Vacuous all([]) would match every note — require at least one real token.
        if not words:
            return f"No notes found matching '{query}' in vault {vault_dir.name}."

        for p in vault_dir.rglob("*.md"):
            if any(part.startswith(".") for part in p.parts):
                continue
            try:
                content_lower = p.read_text(encoding="utf-8").lower()
                if all(w in content_lower for w in words):
                    rel = p.relative_to(vault_dir)
                    results.append(f"• {rel}")
                    if len(results) >= 8:
                        break
            except Exception:
                continue

        if not results:
            return f"No notes found matching '{query}' in vault {vault_dir.name}."
        return f"[VAULT SEARCH: '{query}'] Found notes:\n" + "\n".join(results) + "\n\nUse action='read', note_name='<title>' to inspect any note."

    # -------------------------------------------------------------
    # Action: add_inbox
    # -------------------------------------------------------------
    elif action in ("add_inbox", "inbox", "save_note"):
        if not content and not title:
            return "Error: content or title is required when action='add_inbox'."

        inbox_dir = vault_dir / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d")
        slug_raw = re.sub(r"[^a-zA-Z0-9]+", "-", (title or content[:30]).lower()).strip("-")
        slug = slug_raw[:40] or "voice-capture"
        target_file = inbox_dir / f"{date_str}-{slug}.md"

        frontmatter = (
            f"---\n"
            f"type: inbox-capture\n"
            f"created: {date_str}\n"
            f"source: kurek-voice\n"
            f"status: draft\n"
            f"---\n\n"
        )
        full_text = f"{frontmatter}# {title or 'Voice Capture'}\n\n{content}\n"
        target_file.write_text(full_text, encoding="utf-8")

        return f"Successfully saved to vault inbox: inbox/{target_file.name}"

    return f"Unknown action: '{action}'. Supported actions: 'list_projects', 'read', 'search', 'add_inbox'."


TOOL = {
    "name": "vault_knowledge",
    "description": (
        "Interacts with the nodaysidle-knowledge llm-wiki and Obsidian vault. "
        "Allows searching project documentation, reading concept notes (architecture, PRD, status, roadmaps), "
        "listing active portfolio projects, reading social media / X promotion strategies, "
        "and capturing ideas into the vault inbox."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "The vault action: 'list_projects' (overview of all active projects in the portfolio), "
                    "'read' (read a specific note by title or name), 'search' (keyword search across notes), "
                    "or 'add_inbox' (capture an idea, task, or note into vault inbox)."
                ),
                "enum": ["list_projects", "read", "search", "add_inbox"],
            },
            "note_name": {
                "type": "STRING",
                "description": (
                    "Name or slug of the note to read when action='read' "
                    "(e.g. 'kureksistant-overview', 'cascade-v3-overview', 'nodaysrammar-overview', "
                    "'x-promo-strategy-2026-10-12', 'moc-x-promo', 'nodaysidle-sonora-overview', 'x-promo-strategy')."
                ),
            },
            "query": {
                "type": "STRING",
                "description": "Keywords or search term when action='search'.",
            },
            "title": {
                "type": "STRING",
                "description": "Title when action='add_inbox'.",
            },
            "content": {
                "type": "STRING",
                "description": "Content or details when action='add_inbox'.",
            },
        },
        "required": ["action"],
    },
    "handler": vault_knowledge,
}
