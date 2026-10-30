"""
Memory tool for Kurek.
Allows storing and recalling long-term user facts, preferences, identity, and notes.
Recall searches learned memory, reference facts, Hermes memories (see HERMES_* in .env)
and the daily logs of past conversations.
"""
import re
from datetime import datetime
from pathlib import Path

from core.proposals import propose
from memory import ledger
from memory.config_manager import resolve_hermes_memory_dirs


# Words that appear in nearly every turn; scoring them buries the real hits.
_FILLER = frozenset(
    "about after again all also and any are ask asked before but can could did does done for from "
    "had has have how into its just last let like me more my not now of off on or our out over said "
    "say says so some tell than that the their them then there these they this told to too was we "
    "were what when where which who why will with would you your".split()
)


def _keywords(query: str) -> set[str]:
    return {w for w in re.findall(r"\w+", query.casefold()) if len(w) > 1 and w not in _FILLER}


def _score(text: str, words: set[str]) -> int:
    """How many of the query's keywords the text contains."""
    text = text.casefold()
    return sum(w in text for w in words)


def _search_daily_logs(words: set[str], limit: int = 5) -> list[str]:
    """Past turns from the daily logs that best match the keywords, best match first.

    A daily log is what was said, not what is true, so each hit carries its date."""
    hits = []
    for log in (Path.home() / "memory").glob("????-??-??.md"):
        try:
            turns = log.read_text(encoding="utf-8").split("\n### ")[1:]
        except OSError:
            continue
        for turn in turns:
            if score := _score(turn, words):
                hits.append((score, log.stem, " ".join(turn.split())))
    hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
    return [f"• [Said on {day}] {text[:400]}" for _, day, text in hits[:limit]]


def _resolve_hermes_dir() -> Path | None:
    for candidate in resolve_hermes_memory_dirs():
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def manage_memory(parameters: dict, **kwargs) -> str:
    params = parameters or {}
    action = params.get("action", "remember").lower()
    hermes_dir = _resolve_hermes_dir()

    if action == "recall":
        words = _keywords(params.get("query", ""))
        if not words:
            return "No search query provided."

        found: list[tuple[int, str]] = []

        # 1. Hermes memory files
        if hermes_dir and hermes_dir.exists():
            for filename in ("USER.md", "MEMORY.md"):
                file_path = hermes_dir / filename
                if file_path.exists():
                    try:
                        for block in file_path.read_text(encoding="utf-8").split("§"):
                            clean_block = " ".join(block.split())
                            if score := _score(clean_block, words):
                                found.append((score, f"• [Hermes {filename}] {clean_block}"))
                    except Exception:
                        pass

        # 2. Reference facts (~/MEMORY.md)
        reference = Path.home() / "MEMORY.md"
        if reference.exists():
            try:
                for line in reference.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith("-") and (score := _score(line, words)):
                        found.append((score, f"• [Reference facts] {line.strip()}"))
            except Exception:
                pass

        # 3. The learned-memory ledger
        for row in ledger.active():
            if score := _score(row["text"], words):
                found.append((score, f"• [Learned #{row['id']}] {row['text']}"))

        found.sort(key=lambda f: f[0], reverse=True)
        lines = [line for _, line in found[:6]] + _search_daily_logs(words)
        if not lines:
            return f"No memories or past conversations found matching '{params.get('query', '')}'."
        return f"Found {len(lines)} matches:\n" + "\n".join(lines)

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

    try:
        memory_id = ledger.append(
            category, f"{key}: {value}", f"manage_memory call {datetime.now():%Y-%m-%d}",
            key=key, supersedes=_as_int(params.get("replaces")),
        )
    except ledger.LedgerError as e:
        return f"Nothing was saved: {e}."

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

    # 2. A hard boundary is only proposed; the user promotes it to rules.toml
    if jev_meta and jev_meta.get("kind") == "boundary":
        if propose("manage_memory (jev boundary)", f"{key}: {value}", source="manage_memory tool call"):
            print("[Memory Tool] Boundary proposed in kurek_proposed_rules.md.")

    return f"Remembered: {category}/{key} = {value} (saved as memory #{memory_id}{salience_tag})."


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    "description": "Store or recall persistent facts, personal identity, user preferences, projects, relationships, or notes. Use action='remember' to save something about the user. Use action='recall' to search learned memory, reference facts, Hermes memories and the daily logs of past conversations (each past-conversation hit carries its date).",
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
            "replaces": {
                "type": "INTEGER",
                "description": "For remember: the #id of an earlier learned memory this one corrects or replaces"
            },
            "query": {
                "type": "STRING",
                "description": "For recall: several keywords and synonyms separated by spaces, e.g. 'phone apk android crash rotate'. Any keyword can match; more matches rank higher."
            }
        },
        "required": ["action"]
    },
    "handler": manage_memory,
}
