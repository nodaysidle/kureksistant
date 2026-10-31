"""
core/dream_cycle.py — Nightly Dream & Alignment Synthesis for Muse Memory.

Flow (runs early morning, reflects on the previous day by default):
  1. Reads the target day's dialogue turns from ~/memory/YYYY-MM-DD.md.
  2. Uses DeepSeek-Flash to compose an evocative prose "dream" reflecting that day.
  3. Writes dream to ~/dreams/YYYY-MM-DD.md (with <!-- prompt_hoisted: false -->).
     If the LLM call fails nothing is written, so the next hourly sweep retries.
  4. Turns the day's new facts and rules into *proposals* in
     ~/kurek_proposed_rules.md (core/proposals.py), each marked fact or rule. It
     sees current learned memory and rules first, so anything already known or
     since corrected is not proposed again. It never writes a file that reaches
     the prompt.
  5. Updates ~/ALIGNMENT_STATE.yaml when it exists.
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from core.proposals import PROPOSALS_PATH, propose
from core.rules import RulesError, load_rules
from memory import ledger, reference

HOME = Path.home()
DREAMS_DIR = HOME / "dreams"
MEMORY_DIR = HOME / "memory"
STATE_FILE = HOME / "ALIGNMENT_STATE.yaml"
REPAIRS_FILE = HOME / "REPAIR_THREADS.yaml"


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


def text_from_llm_response(resp, default: str = "") -> str:
    """Normalise query_deepseek return values (str | dict | None) to plain text."""
    if isinstance(resp, str):
        return resp.strip()
    if isinstance(resp, dict):
        return (resp.get("content") or default).strip()
    return default


_NOTHING_NEW = re.compile(r"^\W*(none|nothing|no (new|explicit|standing|additional)\b)", re.IGNORECASE)


def guidance_bullets(text: str) -> list[str]:
    """Bullet lines of an LLM guidance block; headings, prose and "none" answers are dropped."""
    return [b for _, b in guidance_items(text)]


def guidance_items(text: str) -> list[tuple[str, str]]:
    """(kind, bullet) pairs: bullets under a heading that mentions facts are facts,
    everything else is a rule."""
    items, kind = [], "rule"
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("#"):
            kind = "fact" if "fact" in s.casefold() else "rule"
        elif s.startswith(("-", "*", "•")):
            bullet = s.lstrip("-*• ").strip()
            if bullet and not _NOTHING_NEW.match(bullet):
                items.append((kind, bullet))
    return items


def _known_now() -> str:
    """Learned memory and reference facts as they stand today, newest wins."""
    parts = [ledger.prompt_block(reference.MEMORY_FILE_MAX_CHARS), reference.prompt_block()]
    return "\n\n".join(p for p in parts if p).strip()


def _already_known(item: str, known: str) -> bool:
    """True when the proposal's text already appears in memory or the rules."""
    flat = " ".join(item.split()).casefold().strip(" .")
    return len(flat) > 8 and flat in " ".join(known.split()).casefold()


def _existing_rules() -> str:
    try:
        return load_rules().render()
    except RulesError:
        return ""


def execute_dream_cycle(force: bool = False, day: str | None = None) -> dict:
    """Reflect on ``day`` (YYYY-MM-DD; default: yesterday).

    Returns {"status": "success", "date", "dream_file", "proposals_file", "proposed"},
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

    # 4. Propose rules from the day's guidance
    rules_now = _existing_rules()
    known_now = _known_now()
    align_prompt = f"""Review the interactions from {target_day} and extract what is new and lasting:

{recent_content[-4000:]}

Rules the user already has. Do not repeat or rephrase any of them:
{rules_now or "(none)"}

What Kurek knows about the user TODAY (it may have been corrected after {target_day}; it wins over that day's conversation):
{known_now or "(nothing)"}

Return a concise Markdown block with exactly these two sections:
## Facts about the user
- (lasting facts: identity, setup, tools, places, preferences, one per bullet)

## Rules for how Kurek should behave
- (standing instructions the user stated or clearly demonstrated, one per bullet)

Leave out anything the rules or today's knowledge already say, anything they contradict (it was corrected), one-off tasks, and remarks about Kurek's own mistakes. If there is nothing new, return exactly: NONE
"""
    align_resp = query_deepseek(
        messages=[{"role": "user", "content": align_prompt}],
        model="deepseek-flash"
    )
    guidance = text_from_llm_response(align_resp)
    known = f"{rules_now}\n{known_now}"
    proposed = sum(
        propose(f"dream cycle ({target_day})", bullet, source=f"memory/{target_day}.md", kind=kind)
        for kind, bullet in guidance_items(guidance)
        if not _already_known(bullet, known)
    )
    print(f"[Muse Dream] 📝 {proposed} proposal(s) written to {PROPOSALS_PATH.name}", flush=True)

    # 5. Update companion YAML (only if the user created it)
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
        "proposals_file": str(PROPOSALS_PATH),
        "proposed": proposed,
    }
