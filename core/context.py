"""
core/context.py — what the model sees each turn, built from every memory store.

The system prompt is the persona, then [USER RULES], the portfolio anchor and the
memory block (Hermes, ~/MEMORY.md, learned memory). WHO_WINS tells the model how
those blocks rank; the persona embeds it, and it lives here so the ranking and the
blocks it names change together.
"""

from __future__ import annotations

from pathlib import Path

from core import rules
from memory import ledger, reference

PROJECTS_DIR = Path.home() / "dev" / "nodaysidle"

# Marks the history tool note; the speech guard and the reply clean-up match on it.
TOOL_NOTE = "Tool calls in that turn"

WHO_WINS = (
    "WHO WINS WHEN SOURCES DISAGREE (highest first): "
    "1. [USER RULES]. "
    "2. The user's current message. If it goes against a lasting fact in [LEARNED MEMORY] (their terminal, name, city, where files go), do not act at all this turn (no tool calls, not even with the remembered value): say what memory holds, ask once whether it changed, and wait for the answer. Save the change with manage_memory (replaces=<#id>) only after they confirm. Something said only for now ('I'm in Koper today', 'use the phone tonight') you simply follow and never save. "
    "3. [LEARNED MEMORY]. "
    "4. [MUSE_CORE_MEMORY] reference facts and Hermes [USER] / [MEMORY]. If one disagrees with learned memory, follow learned memory and tell the user in one sentence so they can fix it. "
    "5. Earlier chat turns and past conversations found by recall: what was said, never proof of what is true now. "
)

_warned_cutoff = ""


def project_names() -> list[str]:
    """The user's projects: every git repo directly inside PROJECTS_DIR."""
    try:
        return sorted(p.name for p in PROJECTS_DIR.iterdir() if (p / ".git").exists())
    except OSError:
        return []


def history_message(turn: dict) -> dict:
    """A stored turn as a chat message. Assistant turns carry the tools they ran, so a later
    turn can tell whether it really searched instead of guessing from the reply text."""
    content = turn.get("content", "")
    # Only turns that ran tools get the note: a "none" note on every turn taught the model to copy it.
    if turn.get("role") == "assistant" and turn.get("tools"):
        content = f"{content}\n\n({TOOL_NOTE}, recorded by the daemon: {'; '.join(turn['tools'])}.)"
    return {"role": turn.get("role", "user"), "content": content}


def build_messages(persona: str, user_prompt: str, history: list[dict]) -> list[dict]:
    """The chat messages for one turn: system prompt, recent history, then the user's prompt
    with any pinned rules repeated after it."""
    global _warned_cutoff

    sys_prompt = persona
    if rules_block := rules.prompt_block():
        sys_prompt += f"\n\n{rules_block}"
    sys_prompt += (
        "\n\n[NODAYSIDLE PORTFOLIO & KNOWLEDGE VAULT]\n"
        "Vault: nodaysidle-knowledge (Obsidian llm-wiki)\n"
        f"The user's projects (git repos in ~/dev/nodaysidle): {', '.join(project_names()) or 'none found'}.\n"
        "Social Media / X Strategy: wiki/concepts/x-promo-strategy.md, moc-x-promo.md, reel-clip-workflow.md.\n"
        "Whenever asked about portfolio projects, roadmap, what must be done, what is done, or social media strategy, ALWAYS call vault_knowledge(action='read'|'list_projects'|'search') to retrieve ground truth."
    )
    mem_parts = [reference.prompt_block(), ledger.prompt_block(reference.MEMORY_FILE_MAX_CHARS)]
    if mem_block := "\n\n".join(p for p in mem_parts if p).strip():
        sys_prompt += f"\n\n[USER MEMORY & PREFERENCES]\n{mem_block}"

    messages = [{"role": "system", "content": sys_prompt}]
    # Last 10 turns for continuity. Turns from before a memory correction made outside the
    # chat may repeat the old fact, and the model believes the chat over memory, so they go.
    cutoff = ledger.outside_correction_ts()
    recent = [t for t in history if t.get("ts", "") > cutoff] if cutoff else history
    if len(recent) < len(history) and cutoff != _warned_cutoff:
        _warned_cutoff = cutoff
        print(f"[Kurek Memory] WARNING: {len(history) - len(recent)} chat turns from before "
              f"the memory correction of {cutoff} are no longer sent to the model.", flush=True)
    messages += [history_message(turn) for turn in recent[-10:]]
    pinned = rules.pinned_block()
    messages.append({"role": "user", "content": f"{user_prompt}\n\n{pinned}" if pinned else user_prompt})
    return messages
