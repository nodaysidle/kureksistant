"""
actions/vault_knowledge.py — NODAYSIDLE knowledge vault tool for Kurek (the nodaysidle-vault Obsidian vault).
Auto-discovered by core/action_loader.py.

Vault layout used: wiki/hot.md (current context and project list), wiki/index.md (catalog),
wiki/concepts|sessions|sources|entities/<Title-Case-Name>.md, and inbox/. Only wiki/ (minus wiki/meta)
and inbox/ count as knowledge; plans/, graft/, tools/ and hidden folders such as .raw are never read or
searched. A vault in the old layout (wiki/MOC, briefs, artifacts, sources/index, sessions) still works.

Capabilities:
  • 'list_projects': Overview of the active portfolio projects, branches, heads and releases (wiki/hot.md).
  • 'read': Reads one note (project page, owner rule, session) by name, case-insensitive, or by vault path.
  • 'search': Fast keyword search across the knowledge notes.
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
        Path.home() / "dev" / "nodaysidle" / "nodaysidle-vault",
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


# Folders that hold knowledge; the old-layout ones come last so an old vault set by env var still works.
_NOTE_FOLDERS = (
    "wiki/concepts", "wiki/sessions", "wiki/sources", "wiki/entities", "inbox",
    "wiki/MOC", "briefs", "artifacts", "sources/index", "sessions",
)
_KNOWLEDGE_ROOTS = ("wiki", "inbox", "briefs", "artifacts", "sources", "sessions")
_SEARCH_ROOTS = ("wiki", "inbox")
_MAX_CHARS = 8000


def _norm(name: str) -> str:
    """'Cascade V3', 'cascade_v3' and 'Cascade-V3.md' all become 'cascade-v3'."""
    return re.sub(r"[\s_-]+", "-", name.strip().lower().removesuffix(".md")).strip("-")


def _is_knowledge_path(vault_dir: Path, path: Path) -> bool:
    """True if the resolved path is a note inside a knowledge folder (not plans/, .raw/, wiki/meta/ ...)."""
    try:
        parts = path.resolve().relative_to(vault_dir.resolve()).parts
    except ValueError:
        return False
    return (
        len(parts) > 1
        and parts[0] in _KNOWLEDGE_ROOTS
        and not any(p.startswith(".") for p in parts)
        and parts[:2] != ("wiki", "meta")
    )


def _knowledge_files(vault_dir: Path) -> list[Path]:
    """Every .md under wiki/ (minus wiki/meta) and inbox/, skipping hidden entries and symlink escapes."""
    out: list[Path] = []
    for root in _SEARCH_ROOTS:
        base = vault_dir / root
        for dirpath, dirnames, filenames in os.walk(base):
            at_wiki_top = Path(dirpath) == base and root == "wiki"
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not (at_wiki_top and d == "meta"))
            out.extend(
                Path(dirpath) / f for f in sorted(filenames)
                if f.endswith(".md") and not f.startswith(".") and _is_knowledge_path(vault_dir, Path(dirpath) / f)
            )
    return out


def _cap(text: str) -> str:
    if len(text) > _MAX_CHARS:
        return text[:_MAX_CHARS] + "\n\n... [Truncated for voice/context brevity. Request specific sections if needed.]"
    return text


def _commit_capture(vault_dir: Path, rel: str, title: str) -> str | None:
    """Commit only `rel` in the vault's git repo. None: not a git repo. "": committed. Else: the error line."""
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(vault_dir), *args], capture_output=True, text=True, timeout=10)

    try:
        if git("rev-parse", "--is-inside-work-tree").returncode != 0:
            return None
        subject = " ".join(title.split())[:60] or "Voice Capture"
        for args in (("add", "--", rel), ("commit", "-m", f"inbox: {subject} (Kurek capture)", "--", rel)):
            res = git(*args)
            if res.returncode != 0:
                lines = (res.stderr.strip() or res.stdout.strip()).splitlines()
                return (lines[0] if lines else f"git {args[0]} exited {res.returncode}")[:160]
        return ""
    except Exception as exc:  # timeout, git missing, ...: the capture stays on disk, uncommitted
        return (str(exc).splitlines() or [type(exc).__name__])[0][:160] or type(exc).__name__


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
            "Error: NODAYSIDLE knowledge vault directory not found. "
            "Please ensure ~/dev/nodaysidle/nodaysidle-vault exists or set KUREK_VAULT_DIR."
        )

    # -------------------------------------------------------------
    # Action: list_projects
    # -------------------------------------------------------------
    if action in ("list_projects", "projects", "status"):
        wanted = ("wiki/hot.md", "wiki/index.md", "wiki/MOC/moc-nodaysidle-knowledge.md")
        for rel in wanted:
            overview = vault_dir / rel
            if overview.is_file() and _path_within_vault(vault_dir, overview):
                clean = _clean_markdown(overview.read_text(encoding="utf-8"))
                return f"[NODAYSIDLE PORTFOLIO OVERVIEW from {vault_dir.name}]\n{_cap(clean)}"
        return f"Vault found at {vault_dir}, but none of {', '.join(wanted)} exists."

    # -------------------------------------------------------------
    # Action: read
    # -------------------------------------------------------------
    elif action == "read":
        if not note_name:
            return "Error: note_name parameter is required when action='read' (e.g. 'Kureksistant' or 'Cascade-V3')."

        note_path = Path(note_name)
        if note_path.is_absolute() or ".." in note_path.parts:
            return (
                "Error: note_name must be a relative path within the vault; "
                "absolute paths and '..' traversal are not allowed."
            )

        slug = _norm(note_path.name)
        found_file: Path | None = None

        # 1. Exact relative path
        candidate = vault_dir / (note_name if note_name.endswith(".md") else f"{note_name}.md")
        if candidate.is_file() and _is_knowledge_path(vault_dir, candidate):
            found_file = candidate

        # 2. Exact name (case, spaces, hyphens, underscores ignored) in the note folders, in order
        if not found_file and slug:
            for s_folder in _NOTE_FOLDERS:
                folder = vault_dir / s_folder
                if not folder.is_dir():
                    continue
                for c in sorted(folder.glob("*.md")):
                    if _norm(c.stem) == slug and c.is_file() and _is_knowledge_path(vault_dir, c):
                        found_file = c
                        break
                if found_file:
                    break

        # 3. Contains-match on the file name, knowledge folders only; shortest name first
        if not found_file and slug:
            hits = [p for p in _knowledge_files(vault_dir) if slug in _norm(p.stem)]
            if hits:
                found_file = min(hits, key=lambda p: len(p.stem))

        if not found_file:
            # Suggest matching notes
            words = [w for w in slug.split("-") if len(w) > 2]
            matches = [p.stem for p in _knowledge_files(vault_dir) if any(w in _norm(p.stem) for w in words)][:5]
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

        return f"[VAULT NOTE: {rel_path}]\n{_cap(cleaned)}"

    # -------------------------------------------------------------
    # Action: search
    # -------------------------------------------------------------
    elif action == "search":
        if not query:
            return "Error: query parameter is required when action='search'."

        results = []
        # Prefer fast ripgrep if available
        roots = [r for r in _SEARCH_ROOTS if (vault_dir / r).is_dir()]
        if roots and shutil.which("rg"):
            try:
                cmd = [
                    "rg",
                    "--no-config",
                    "-i",
                    "--max-count", "3",
                    "--glob", "!.*",
                    "--glob", "!wiki/meta/**",  # relative to cwd=vault_dir
                    "-e", query,  # after -e, a query starting with "-" is text, never an option
                    *roots,
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=3.0, cwd=vault_dir)
                if res.returncode == 0 and res.stdout.strip():
                    lines = res.stdout.strip().splitlines()[:15]
                    formatted_matches = []
                    for line in lines:
                        if ":" in line:
                            path_str, text_str = line.split(":", 1)
                            rel = Path(path_str)
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

        for p in _knowledge_files(vault_dir):
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

        frontmatter = (
            f"---\n"
            f"type: inbox-capture\n"
            f"created: {date_str}\n"
            f"source: kurek-voice\n"
            f"status: draft\n"
            f"---\n\n"
        )
        full_text = f"{frontmatter}# {title or 'Voice Capture'}\n\n{content}\n"
        # Never overwrite an earlier capture with the same title on the same day.
        n = 1
        while True:
            target_file = inbox_dir / (f"{date_str}-{slug}.md" if n == 1 else f"{date_str}-{slug}-{n}.md")
            try:
                with open(target_file, "x", encoding="utf-8") as f:
                    f.write(full_text)
                break
            except FileExistsError:
                n += 1

        rel = f"inbox/{target_file.name}"
        err = _commit_capture(vault_dir, rel, title)
        if err is None:
            return f"Successfully saved to vault inbox: {rel}"
        if not err:
            return f"Saved to vault inbox and committed: {rel}"
        return f"Saved to vault inbox: {rel}, but the commit failed ({err}). The file is on disk and uncommitted."

    return f"Unknown action: '{action}'. Supported actions: 'list_projects', 'read', 'search', 'add_inbox'."


TOOL = {
    "name": "vault_knowledge",
    "description": (
        "Interacts with the NODAYSIDLE knowledge vault (Obsidian llm-wiki). "
        "Allows searching project documentation, reading project notes (architecture, status, roadmaps, owner rules), "
        "listing active portfolio projects with their branches and releases, "
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
                    "(e.g. 'Kureksistant', 'Cascade-V3', 'Reverseuno', 'Nodaysidle-Ecosystem-Overview'). "
                    "Case, spaces and hyphens do not matter."
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
