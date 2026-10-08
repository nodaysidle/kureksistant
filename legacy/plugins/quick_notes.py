"""
Drop-in JARVIS plugin: Quick Voice Notes & Scratchpad.
Saves, searches, and reads timestamped Markdown notes in ~/Documents/JARVIS_Notes.
"""
from datetime import datetime
from pathlib import Path

PLUGIN = {
    "name": "quick_notes",
    "description": (
        "Quick notes and voice scratchpad. Use this to take notes, write down ideas, "
        "read back recent notes, or search notes. "
        "Actions: 'add' (save note), 'read_last' (read most recent note), "
        "'list' (list recent note titles), 'search' (search notes)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'add' | 'read_last' | 'list' | 'search' (default: add)"
            },
            "content": {
                "type": "STRING",
                "description": "Content of the note to add, or query to search for"
            },
            "title": {
                "type": "STRING",
                "description": "Optional title for the note"
            }
        },
        "required": []
    }
}

def _get_notes_dir() -> Path:
    d = Path.home() / "Documents" / "JARVIS_Notes"
    d.mkdir(parents=True, exist_ok=True)
    return d

def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = (params.get("action") or "add").lower().strip()
    content = (params.get("content") or "").strip()
    title = (params.get("title") or "").strip()

    notes_dir = _get_notes_dir()

    if action == "add":
        if not content:
            return "Please tell me what you would like to note down, sir."
        now = datetime.now()
        stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
        date_str = now.strftime("%Y-%m-%d %I:%M %p")
        safe_title = (title or content[:30]).replace("/", "-").replace("\\", "-").strip()
        filename = f"{stamp}_{safe_title[:40]}.md"
        filepath = notes_dir / filename

        note_body = f"# {title or 'Note'}\n*Saved on {date_str}*\n\n{content}\n"
        filepath.write_text(note_body, encoding="utf-8")
        if player:
            player.write_log(f"📝 Note saved: {filepath.name}")
        return f"Note recorded: '{content[:50]}...', sir."

    elif action == "read_last":
        files = sorted(notes_dir.glob("*.md"), reverse=True)
        if not files:
            return "You have no saved notes yet, sir."
        last_file = files[0]
        text = last_file.read_text(encoding="utf-8").strip()
        if player:
            player.show_content(f"LAST NOTE: {last_file.stem}", text)
        return f"Your last note from {last_file.stem[:10]} says: {text[:200]}"

    elif action == "list":
        files = sorted(notes_dir.glob("*.md"), reverse=True)
        if not files:
            return "No notes found, sir."
        lines = [f"Found {len(files)} notes:"]
        for i, f in enumerate(files[:5], 1):
            lines.append(f"{i}. {f.stem}")
        out = "\n".join(lines)
        if player:
            player.show_content("JARVIS NOTES", out)
        return out

    elif action == "search":
        if not content:
            return "Please provide a keyword to search for, sir."
        matches = []
        for f in notes_dir.glob("*.md"):
            try:
                body = f.read_text(encoding="utf-8")
                if content.lower() in body.lower() or content.lower() in f.stem.lower():
                    matches.append((f.name, body[:120]))
            except Exception:
                pass
        if not matches:
            return f"No notes matched '{content}', sir."
        lines = [f"Notes matching '{content}':"]
        for fname, preview in matches[:5]:
            lines.append(f"• {fname}:\n  {preview.replace(chr(10), ' ')}")
        out = "\n".join(lines)
        if player:
            player.show_content("SEARCH RESULTS", out)
        return out

    return f"Action '{action}' complete."
