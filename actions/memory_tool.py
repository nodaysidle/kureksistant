"""
Memory tool for Kurek.
Allows storing, recalling and forgetting long-term user facts, preferences, identity, and notes.
Recall searches learned memory, reference facts, Hermes memories (see HERMES_* in .env)
and the daily logs of past conversations. Hermes files are read only, never written here.
"""
import re
from datetime import datetime
from pathlib import Path

from core.proposals import propose
from memory import ledger, recall_index, reference


# Words that appear in nearly every turn; scoring them buries the real hits.
_FILLER = frozenset(
    "about after again all also and any are ask asked before but can could did does done for from "
    "had has have how into its just last let like me more my not now of off on or our out over said "
    "say says so some tell than that the their them then there these they this told to too was we "
    "were what when where which who why will with would you your".split()
)

# The model names a category; the ledger stores one of four kinds.
_KINDS = {
    "identity": "fact",
    "preferences": "preference",
    "projects": "fact",
    "relationships": "fact",
    "wishes": "preference",
    "notes": "note",
}


def _keywords(query: str) -> set[str]:
    return {w for w in re.findall(r"\w+", query.casefold()) if len(w) > 1 and w not in _FILLER}


def _score(text: str, words: set[str]) -> int:
    """How many of the query's keywords begin some word in the text.

    Matching whole-word prefixes keeps "crash" on "crashed" without letting
    "art" hit "start"."""
    text = text.casefold()
    return sum(bool(re.search(rf"\b{re.escape(w)}", text)) for w in words)


def _search_daily_logs(words: set[str], limit: int = 5) -> list[str]:
    """Past turns from the daily logs that best match the keywords, best match first.

    A daily log is what was said, not what is true, so each hit carries its date."""
    try:
        hits = recall_index.search(words, limit=limit)
    except Exception as e:
        print(f"[Memory Tool] daily-log index unavailable ({e}); scanning the logs.", flush=True)
        return _scan_daily_logs(words, limit)
    return [f"• [Said on {day}] {text[:400]}" for day, text in hits]


def _scan_daily_logs(words: set[str], limit: int = 5) -> list[str]:
    """The pre-index fallback: read every daily log and score it in Python."""
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


def _recall_line(row) -> str:
    if row["kind"] == "retract":
        return f"• [Retracted #{row['supersedes']}] {row['text']}"
    return f"• [Learned #{row['id']}] {row['text']}"


def _forget(params: dict) -> str:
    target = _as_int(params.get("replaces"))
    if target is None:
        return "Say which memory to forget: pass its #id as replaces."
    reason = params.get("value", "").strip() or "forgotten by the user"
    try:
        ledger.retract(target, reason, f"manage_memory call {datetime.now():%Y-%m-%d}")
    except ledger.LedgerError as e:
        return f"Nothing was forgotten: {e}."
    return f"Forgot memory #{target}."


def manage_memory(parameters: dict, **kwargs) -> str:
    params = parameters or {}
    action = params.get("action", "remember").lower()

    if action == "recall":
        words = _keywords(params.get("query", ""))
        if not words:
            return "No search query provided."

        found: list[tuple[int, str]] = []

        # 1. Hermes memories and reference facts (~/MEMORY.md)
        for label, text in reference.entries():
            if score := _score(text, words):
                found.append((score, f"• [{label}] {text}"))

        # 2. The learned-memory ledger, retractions included so recall shows what was withdrawn
        for row in ledger.active():
            if score := _score(row["text"], words):
                found.append((score, _recall_line(row)))

        found.sort(key=lambda f: f[0], reverse=True)
        lines = [line for _, line in found[:6]] + _search_daily_logs(words)
        if not lines:
            return f"No memories or past conversations found matching '{params.get('query', '')}'."
        return f"Found {len(lines)} matches:\n" + "\n".join(lines)

    if action == "forget":
        return _forget(params)

    # Default: remember
    key = params.get("key", "").strip()
    value = params.get("value", "").strip()
    category = params.get("category", "notes").strip().lower()
    kind = _KINDS.get(category, "note")

    if not key or not value:
        return "Please provide both a key and a value to remember."

    # Jev System One Triage (calibrated snap judgment)
    jev_meta = _triage_with_jev(key, value)
    if jev_meta:
        inferred_kind = jev_meta.get("kind", kind)
        if inferred_kind in ("preference", "boundary", "fact"):
            kind = inferred_kind

    try:
        memory_id = ledger.append(
            kind, f"{key}: {value}", f"manage_memory call {datetime.now():%Y-%m-%d}",
            key=key, supersedes=_as_int(params.get("replaces")),
        )
    except ledger.LedgerError as e:
        return f"Nothing was saved: {e}."

    # A hard boundary is only proposed; the user promotes it to rules.toml
    if jev_meta and jev_meta.get("kind") == "boundary":
        if propose("manage_memory (jev boundary)", f"{key}: {value}", source="manage_memory tool call"):
            print("[Memory Tool] Boundary proposed in kurek_proposed_rules.md.")

    return f"Remembered: {kind}/{key} = {value} (saved as memory #{memory_id})."


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
        from typesafe_sdk import TypeSafeClient, Choice, Score
        client = TypeSafeClient()
        resp = client.system_one(
            state={"key": key, "value": value},
            questions={
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
            "kind": resp.answers["kind"].choice,
            "salience": resp.answers["salience"].score,
            "confidence": resp.answers["salience"].confidence
        }
    except Exception as e:
        print(f"[Memory Tool Jev Notice] {e}")
        return None


TOOL = {
    "name": "manage_memory",
    "description": "Store, recall or forget persistent facts, personal identity, user preferences, projects, relationships, or notes. Use action='remember' to save something about the user. Use action='recall' to search learned memory, reference facts, Hermes memories and the daily logs of past conversations (each past-conversation hit carries its date). Use action='forget' with the #id of a learned memory to withdraw it, keeping the reason. Pass replaces=<#id> to correct an earlier learned memory.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'remember' to save a new fact, 'recall' to search stored facts, or 'forget' to withdraw a memory by #id",
                "enum": ["remember", "recall", "forget"]
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
                "description": "The fact or detail to remember; for forget, why it is being withdrawn"
            },
            "replaces": {
                "type": "INTEGER",
                "description": "For remember: the #id of an earlier learned memory this one corrects or replaces. For forget: the #id of the memory to withdraw"
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
