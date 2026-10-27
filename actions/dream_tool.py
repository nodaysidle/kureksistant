"""
actions/dream_tool.py — Muse Memory nightly dream & alignment synthesis tool.
Auto-discovered by core/action_loader.py.

Capabilities:
  • 'dream': Triggers the Muse Memory reflection cycle. Writes ~/dreams/YYYY-MM-DD.md
             and writes rule proposals to ~/kurek_proposed_rules.md.
"""
from __future__ import annotations

from core.dream_cycle import execute_dream_cycle


def dream_tool(parameters: dict | None = None, **kwargs) -> str:
    params = parameters or {}
    force = bool(params.get("force", False))

    res = execute_dream_cycle(force=force)
    if res.get("status") == "skipped":
        return f"{res['message']} (Pass force=True to re-synthesize)."
    if res.get("status") == "error":
        return (f"Dream synthesis failed for {res.get('date')}: {res.get('message')}. "
                "Nothing was written; it is safe to try again.")

    return f"Nightly dream synthesis complete for {res.get('date')}. Journal written to ~/dreams/{res.get('date')}.md and {res.get('proposed', 0)} rule proposal(s) added to ~/kurek_proposed_rules.md for the user to review."


TOOL = {
    "name": "dream_tool",
    "description": "Executes the Muse Memory nightly dream and reflection cycle. Synthesizes the target day's daily log (yesterday by default) into an atmospheric dream journal (~/dreams/YYYY-MM-DD.md) and writes candidate rules to ~/kurek_proposed_rules.md for the user to approve; it never changes the active rules.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "force": {
                "type": "BOOLEAN",
                "description": "If true, overwrites the existing dream journal for that day."
            }
        }
    },
    "handler": dream_tool,
}
