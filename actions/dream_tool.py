"""
actions/dream_tool.py — Muse Memory nightly dream & alignment synthesis tool.
Auto-discovered by core/action_loader.py.

Capabilities:
  • 'dream': Triggers the Muse Memory reflection cycle. Writes ~/dreams/YYYY-MM-DD.md
             and distills standing guidance into ~/ALIGNMENT_SYNTHESIS.md.
"""
from __future__ import annotations

from core.dream_cycle import execute_dream_cycle


def dream_tool(parameters: dict | None = None, **kwargs) -> str:
    params = parameters or {}
    force = bool(params.get("force", False))

    res = execute_dream_cycle(force=force)
    if res.get("status") == "skipped":
        return f"{res['message']} (Pass force=True to re-synthesize)."

    return f"Nightly dream synthesis complete for {res.get('date')}. Journal written to ~/dreams/{res.get('date')}.md and standing guidance updated in ~/ALIGNMENT_SYNTHESIS.md."


TOOL = {
    "name": "dream_tool",
    "description": "Executes the Muse Memory nightly dream and reflection cycle. Synthesizes today's daily log into an atmospheric dream journal (~/dreams/YYYY-MM-DD.md) and updates standing behavioral guidance in ~/ALIGNMENT_SYNTHESIS.md.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "force": {
                "type": "BOOLEAN",
                "description": "If true, overwrites existing dream journal for today."
            }
        }
    },
    "handler": dream_tool,
}
