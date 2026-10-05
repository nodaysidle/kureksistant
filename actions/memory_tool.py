"""
Memory tool for Kurek / JARVIS.
Allows storing and recalling long-term user facts, preferences, identity, and notes.
Synchronizes with both JARVIS memory and Hermes memory (/Volumes/omarchyuser/26MaySymlink/.hermes/memories).
"""
from pathlib import Path
from memory.memory_manager import remember, search_memory, load_memory

HERMES_CANDIDATES = [
    Path.home() / ".hermes" / "profiles" / "eldio" / "memories",
    Path.home() / ".hermes" / "memories",
    Path("/Volumes/omarchyuser/26MaySymlink/.hermes/memories"),
]

def _resolve_hermes_dir() -> Path | None:
    for candidate in HERMES_CANDIDATES:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def manage_memory(parameters: dict, **kwargs) -> str:
    params = parameters or {}
    action = params.get("action", "remember").lower()
    hermes_dir = _resolve_hermes_dir()

    if action == "recall":
        query = params.get("query", "").strip()
        if not query:
            return "No search query provided."

        found_lines = []

        # 1. Search Hermes memory files
        if hermes_dir and hermes_dir.exists():
            for filename in ("USER.md", "MEMORY.md"):
                file_path = hermes_dir / filename
                if file_path.exists():
                    try:
                        content = file_path.read_text(encoding="utf-8")
                        for block in content.split("§"):
                            if query.lower() in block.lower():
                                clean_block = " ".join(block.split()).strip()
                                if clean_block:
                                    found_lines.append(f"• [Hermes {filename}] {clean_block}")
                    except Exception:
                        pass

        # 2. Search Muse Memory files (~/MEMORY.md and ~/memory/bank/)
        muse_core = Path.home() / "MEMORY.md"
        if muse_core.exists():
            try:
                for line in muse_core.read_text(encoding="utf-8").splitlines():
                    if query.lower() in line.lower() and line.strip().startswith("-"):
                        found_lines.append(f"• [Muse Core] {line.strip()}")
            except Exception:
                pass

        muse_bank = Path.home() / "memory" / "bank"
        if muse_bank.exists():
            try:
                for bfile in muse_bank.glob("*.md"):
                    for line in bfile.read_text(encoding="utf-8").splitlines():
                        if query.lower() in line.lower() and line.strip().startswith("-"):
                            found_lines.append(f"• [Muse {bfile.stem}] {line.strip()}")
            except Exception:
                pass

        # 3. Search local memory
        local_results = search_memory(query)
        if local_results and "Nothing stored" not in local_results and "I have not stored" not in local_results:
            found_lines.append(f"• [Local Memory]\n{local_results}")

        if not found_lines:
            return f"No memories found matching '{query}'."

        return f"Found {len(found_lines)} matching memories:\n" + "\n".join(found_lines[:6])

    # Default: remember
    key = params.get("key", "").strip()
    value = params.get("value", "").strip()
    category = params.get("category", "notes").strip().lower()

    if not key or not value:
        return "Please provide both a key and a value to remember."

    # Jev System One Triage (calibrated snap judgment)
    jev_meta = _triage_with_jev(key, value)
    salience_tag = ""
    if jev_meta:
        inferred_kind = jev_meta.get("kind", category)
        if inferred_kind in ("preference", "boundary", "fact"):
            category = inferred_kind
        salience_tag = f" [salience:{jev_meta['salience']:.1f}|conf:{jev_meta['confidence']:.2f}]"

    local_res = remember(key=key, value=value, category=category)

    # 1. Sync to Hermes MEMORY.md if available
    try:
        if hermes_dir:
            mem_file = hermes_dir / "MEMORY.md"
            if mem_file.exists() and mem_file.is_file():
                entry = f"\n§\n**[{category.upper()}] {key}:** {value}{salience_tag}\n"
                with open(mem_file, "a", encoding="utf-8") as f:
                    f.write(entry)
    except Exception as e:
        print(f"[Memory Tool] Hermes sync notice: {e}")

    # 2. Sync to Muse ~/MEMORY.md
    try:
        muse_file = Path.home() / "MEMORY.md"
        if muse_file.exists():
            entry = f"- [{category.upper()}] {key}: {value}{salience_tag}\n"
            with open(muse_file, "a", encoding="utf-8") as f:
                f.write(entry)
    except Exception as e:
        print(f"[Memory Tool] Muse sync notice: {e}")

    # 3. If Jev detected a hard boundary, hoist to ~/ALIGNMENT_SYNTHESIS.md
    if jev_meta and jev_meta.get("kind") == "boundary":
        try:
            align_file = Path.home() / "ALIGNMENT_SYNTHESIS.md"
            if align_file.exists():
                boundary_entry = f"- **[BOUNDARY] {key}:** {value}\n"
                with open(align_file, "a", encoding="utf-8") as f:
                    f.write(boundary_entry)
                print(f"[Memory Tool] Boundary hoisted to ALIGNMENT_SYNTHESIS.md by Jev.")
        except Exception as e:
            print(f"[Memory Tool] Alignment hoist notice: {e}")

    return f"{local_res} (synced to Hermes and Muse MEMORY.md{salience_tag})."


def _triage_with_jev(key: str, value: str) -> dict | None:
    try:
        import os
        from memory.config_manager import load_api_keys
        load_api_keys()
        if not os.environ.get("TYPESAFE_API_KEY"):
            return None
        from typesafe_sdk import TypeSafeClient, Noul, Choice, Score
        client = TypeSafeClient()
        resp = client.system_one(
            state={"key": key, "value": value},
            questions={
                "is_durable": Noul(
                    instructions="Does `value` assert a permanent personal preference, identity detail, system rule, or boundary that should be kept indefinitely?"
                ),
                "kind": Choice(
                    instructions="What category best describes this information?",
                    criteria={
                        "preference": "User likes, dislikes, habits, or technical preferences",
                        "boundary": "Strict operational rule, forbidden behavior, or hard constraint",
                        "fact": "Static technical, hardware, location, or project detail",
                        "transient": "Temporary note, short-term task, or conversational passing detail",
                        "other": "None of the above"
                    }
                ),
                "salience": Score(
                    instructions="How critical is this memory for future interactions?",
                    criteria=[
                        "Disposable note or temporary context",
                        "Useful contextual detail",
                        "Permanent core preference, strict boundary, or system constraint"
                    ]
                )
            }
        )
        return {
            "is_durable": resp.answers["is_durable"].noul > 0.65,
            "kind": resp.answers["kind"].choice,
            "salience": resp.answers["salience"].score,
            "confidence": resp.answers["salience"].confidence
        }
    except Exception as e:
        print(f"[Memory Tool Jev Notice] {e}")
        return None


TOOL = {
    "name": "manage_memory",
    "description": "Store or recall persistent facts, personal identity, user preferences, projects, relationships, or notes. Use action='remember' to save something about the user, or action='recall' to search stored knowledge (including Hermes memories).",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'remember' to save a new fact, or 'recall' to search stored facts",
                "enum": ["remember", "recall"]
            },
            "category": {
                "type": "STRING",
                "description": "Category for remember: 'identity', 'preferences', 'projects', 'relationships', 'wishes', or 'notes'",
                "enum": ["identity", "preferences", "projects", "relationships", "wishes", "notes"]
            },
            "key": {
                "type": "STRING",
                "description": "Short descriptor key (e.g. 'favorite_coffee', 'sister_name', 'current_project')"
            },
            "value": {
                "type": "STRING",
                "description": "The fact or detail to remember"
            },
            "query": {
                "type": "STRING",
                "description": "Keyword to search for when action is 'recall'"
            }
        },
        "required": ["action"]
    },
    "handler": manage_memory,
}
