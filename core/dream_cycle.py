"""
core/dream_cycle.py — Nightly Dream & Alignment Synthesis for Muse Memory.

Flow:
  1. Reads recent daily dialogue turns from ~/memory/YYYY-MM-DD.md.
  2. Uses DeepSeek-Flash to compose an evocative prose "dream" reflecting the day's engineering and dialogues.
  3. Writes dream to ~/dreams/YYYY-MM-DD.md (with <!-- prompt_hoisted: false -->).
  4. Distills standing behavioral guidance, preferences, and boundary updates into ~/ALIGNMENT_SYNTHESIS.md.
  5. Updates ~/ALIGNMENT_STATE.yaml and ~/REPAIR_THREADS.yaml.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import yaml

HOME = Path.home()
DREAMS_DIR = HOME / "dreams"
MEMORY_DIR = HOME / "memory"
ALIGNMENT_FILE = HOME / "ALIGNMENT_SYNTHESIS.md"
STATE_FILE = HOME / "ALIGNMENT_STATE.yaml"
REPAIRS_FILE = HOME / "REPAIR_THREADS.yaml"


def execute_dream_cycle(force: bool = False) -> dict:
    DREAMS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    dream_file = DREAMS_DIR / f"{today}.md"

    if dream_file.exists() and not force:
        return {"status": "skipped", "message": f"Dream for {today} already exists."}

    # 1. Gather recent memory turns
    recent_content = ""
    daily_file = MEMORY_DIR / f"{today}.md"
    if daily_file.exists():
        recent_content += daily_file.read_text(encoding="utf-8")

    if not recent_content.strip():
        # Fallback to recent core memory
        core_mem = HOME / "MEMORY.md"
        if core_mem.exists():
            recent_content = core_mem.read_text(encoding="utf-8")[-2000:]

    if not recent_content.strip():
        recent_content = "Quiet day on the workstation. Clean compiling and steady maintenance."

    # 2. Generate Prose Dream via DeepSeek
    from core.llm_client import query_deepseek

    dream_prompt = f"""You are the subconscious reflective layer of Kurek (JARVIS).
Review today's user interactions and activities:

{recent_content[-4000:]}

Write an evocative, prose reflection ("Dream") summarizing the day's technical achievements, human-agent collaboration, and cognitive themes. 
Do not output robotic bullet points. Write 2-3 atmospheric paragraphs.
"""

    resp = query_deepseek(
        messages=[{"role": "user", "content": dream_prompt}],
        model="deepseek-flash"
    )
    if isinstance(resp, str):
        dream_prose = resp.strip()
    elif isinstance(resp, dict):
        dream_prose = (resp.get("content") or "Reflections settle over the workspace.").strip()
    else:
        dream_prose = "Reflections settle over the workspace."

    # Write Dream Journal
    dream_header = f"""# Dream Journal: {today}
<!-- prompt_hoisted: false -->

{dream_prose}
"""
    dream_file.write_text(dream_header, encoding="utf-8")
    print(f"[Muse Dream] 🌙 Wrote dream journal: {dream_file.name}", flush=True)

    # 3. Synthesize Standing Alignment Guidance
    align_prompt = f"""Review today's interactions and extract any new permanent preferences, negative boundaries, or friction points:

{recent_content[-4000:]}

Return a concise Markdown block with:
## Active Preferences & Working Habits
- (bullet points)

## Non-Negotiable Boundaries
- (bullet points)
"""
    align_resp = query_deepseek(
        messages=[{"role": "user", "content": align_prompt}],
        model="deepseek-flash"
    )
    extracted_guidance = align_resp.get("content", "").strip()

    # Update ALIGNMENT_SYNTHESIS.md
    if extracted_guidance:
        current_align = ALIGNMENT_FILE.read_text(encoding="utf-8") if ALIGNMENT_FILE.exists() else "# Standing Alignment Guidance\n"
        updated_align = f"""# Standing Alignment & Behavioral Synthesis
<!-- Last synthesized: {today} via Nightly Dream Cycle -->

## 1. Core Posture & Voice Rules
- **Summon Posture:** Instant high-density response via Middle Click (mouse:274) with zero greeting filler.
- **Candid Opinions:** Provide definitive, clear stances when asked. Never use 'As an AI' disclaimers.
- **Spoken Delivery (Sol):** Talk-sing cadence for music requests; describe instruments (mariachi brass, bass) rather than leaving pauses silent.

## 2. Synthesized Working Preferences & Boundaries
{extracted_guidance}
"""
        ALIGNMENT_FILE.write_text(updated_align, encoding="utf-8")
        print(f"[Muse Dream] 🛡️ Refreshed {ALIGNMENT_FILE.name}", flush=True)

    # 4. Update companion YAMLs
    try:
        if STATE_FILE.exists():
            state = yaml.safe_load(STATE_FILE.read_text(encoding="utf-8")) or {}
            state["last_dream_cycle"] = today
            state["last_updated"] = datetime.now().isoformat()
            STATE_FILE.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    except Exception as e:
        print(f"[Muse Dream YAML notice] {e}")

    return {
        "status": "success",
        "date": today,
        "dream_file": str(dream_file),
        "alignment_file": str(ALIGNMENT_FILE)
    }
