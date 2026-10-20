"""
core/dream_cycle.py — Nightly Dream & Alignment Synthesis for Muse Memory.

Flow (runs early morning, reflects on the previous day by default):
  1. Reads the target day's dialogue turns from ~/memory/YYYY-MM-DD.md.
  2. Uses DeepSeek-Flash to compose an evocative prose "dream" reflecting that day.
  3. Writes dream to ~/dreams/YYYY-MM-DD.md (with <!-- prompt_hoisted: false -->).
     If the LLM call fails nothing is written, so the next hourly sweep retries.
  4. Distills standing behavioral guidance into ~/ALIGNMENT_SYNTHESIS.md, carrying
     forward every "**[BOUNDARY]**" line under "## Persistent Boundaries".
  5. Updates ~/ALIGNMENT_STATE.yaml when it exists.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import yaml

HOME = Path.home()
DREAMS_DIR = HOME / "dreams"
MEMORY_DIR = HOME / "memory"
ALIGNMENT_FILE = HOME / "ALIGNMENT_SYNTHESIS.md"
STATE_FILE = HOME / "ALIGNMENT_STATE.yaml"
REPAIRS_FILE = HOME / "REPAIR_THREADS.yaml"

BOUNDARY_MARKER = "**[BOUNDARY]**"
PERSISTENT_SECTION = "## Persistent Boundaries"
_NO_BOUNDARIES = "- (none recorded yet)"


def _atomic_write_text(path: Path, text: str) -> None:
    """Write via a same-directory temp file + fsync + os.replace (mode 0600 when new)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        mode = 0o600
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def extract_persistent_boundaries(alignment_text: str) -> list[str]:
    """Collect boundary lines to carry forward: every "**[BOUNDARY]**" line plus
    every bullet already under "## Persistent Boundaries". Exact-line dedupe, order kept."""
    out: list[str] = []
    seen: set[str] = set()
    in_section = False
    for raw in alignment_text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("#"):
            in_section = stripped.lower() == PERSISTENT_SECTION.lower()
            continue
        if not stripped:
            continue
        keep = BOUNDARY_MARKER in stripped or (
            in_section and stripped.startswith(("-", "*")) and stripped != _NO_BOUNDARIES
        )
        if keep:
            if stripped not in seen:
                seen.add(stripped)
                out.append(stripped)
    return out


def text_from_llm_response(resp, default: str = "") -> str:
    """Normalise query_deepseek return values (str | dict | None) to plain text."""
    if isinstance(resp, str):
        return resp.strip()
    if isinstance(resp, dict):
        return (resp.get("content") or default).strip()
    return default


def execute_dream_cycle(force: bool = False, day: str | None = None) -> dict:
    """Reflect on ``day`` (YYYY-MM-DD; default: yesterday).

    Returns {"status": "success", "date", "dream_file", "alignment_file"},
    {"status": "skipped", "date", "message", "dream_file"} or
    {"status": "error", "date", "message"} (nothing written; safe to retry).
    """
    target_day = day or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    dream_file = DREAMS_DIR / f"{target_day}.md"

    if dream_file.exists() and not force:
        return {
            "status": "skipped",
            "date": target_day,
            "dream_file": str(dream_file),
            "message": f"Dream for {target_day} already exists.",
        }

    # 1. Gather the target day's memory turns
    recent_content = ""
    daily_file = MEMORY_DIR / f"{target_day}.md"
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
Review the user interactions and activities from {target_day}:

{recent_content[-4000:]}

Write an evocative, prose reflection ("Dream") summarizing the day's technical achievements, human-agent collaboration, and cognitive themes.
Do not output robotic bullet points. Write 2-3 atmospheric paragraphs.
"""

    resp = query_deepseek(
        messages=[{"role": "user", "content": dream_prompt}],
        model="deepseek-flash"
    )
    dream_prose = text_from_llm_response(resp)
    if not dream_prose:
        msg = f"DeepSeek returned no dream for {target_day}; will retry on the next sweep."
        print(f"[Muse Dream] {msg}", flush=True)
        return {"status": "error", "date": target_day, "message": msg}

    # Write Dream Journal
    DREAMS_DIR.mkdir(parents=True, exist_ok=True)
    dream_header = f"""# Dream Journal: {target_day}
<!-- prompt_hoisted: false -->

{dream_prose}
"""
    _atomic_write_text(dream_file, dream_header)
    print(f"[Muse Dream] 🌙 Wrote dream journal: {dream_file.name}", flush=True)

    # 3. Synthesize Standing Alignment Guidance
    align_prompt = f"""Review the interactions from {target_day} and extract any new permanent preferences, negative boundaries, or friction points:

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
    extracted_guidance = text_from_llm_response(align_resp)

    # Update ALIGNMENT_SYNTHESIS.md, carrying forward boundaries hoisted during the day.
    if extracted_guidance:
        current_align = ALIGNMENT_FILE.read_text(encoding="utf-8") if ALIGNMENT_FILE.exists() else ""
        boundaries = extract_persistent_boundaries(current_align)
        boundary_block = "\n".join(boundaries) if boundaries else _NO_BOUNDARIES
        updated_align = f"""# Standing Alignment & Behavioral Synthesis
<!-- Last synthesized: {target_day} via Nightly Dream Cycle -->

{PERSISTENT_SECTION}
{boundary_block}

## 1. Core Posture & Voice Rules
- **Summon Posture:** Instant high-density response via Middle Click (mouse:274) with zero greeting filler.
- **Candid Opinions:** Provide definitive, clear stances when asked. Never use 'As an AI' disclaimers.
- **Spoken Delivery (Sol):** Talk-sing cadence for music requests; describe instruments (mariachi brass, bass) rather than leaving pauses silent.

## 2. Synthesized Working Preferences & Boundaries
{extracted_guidance}
"""
        _atomic_write_text(ALIGNMENT_FILE, updated_align)
        print(f"[Muse Dream] 🛡️ Refreshed {ALIGNMENT_FILE.name} ({len(boundaries)} persistent boundaries kept)", flush=True)
    else:
        print("[Muse Dream] No alignment guidance returned; ALIGNMENT_SYNTHESIS.md left unchanged.", flush=True)

    # 4. Update companion YAML (only if the user created it)
    if STATE_FILE.exists():
        try:
            state = yaml.safe_load(STATE_FILE.read_text(encoding="utf-8")) or {}
            state["last_dream_cycle"] = target_day
            state["last_updated"] = datetime.now().isoformat()
            _atomic_write_text(STATE_FILE, yaml.safe_dump(state, sort_keys=False))
        except Exception as e:
            print(f"[Muse Dream] Could not update {STATE_FILE.name}: {e}", flush=True)
    else:
        print(f"[Muse Dream] {STATE_FILE.name} not present; skipping state update.", flush=True)

    return {
        "status": "success",
        "date": target_day,
        "dream_file": str(dream_file),
        "alignment_file": str(ALIGNMENT_FILE)
    }
