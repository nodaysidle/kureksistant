"""CI check: systemd unit memory envelope fits the ~333MB Whisper footprint."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "desktop" / "kurek.service"


def _parse_memory_value(raw: str) -> int:
    """Parse systemd memory sizes like 400M / 600M into megabytes."""
    match = re.fullmatch(r"(\d+)([KMG])?", raw.strip(), flags=re.IGNORECASE)
    assert match, f"unrecognised memory value: {raw!r}"
    amount = int(match.group(1))
    unit = (match.group(2) or "M").upper()
    scale = {"K": 1 / 1024, "M": 1, "G": 1024}[unit]
    return int(amount * scale)


def test_kurek_service_memory_envelope() -> None:
    assert UNIT.is_file(), f"missing unit file: {UNIT}"
    text = UNIT.read_text(encoding="utf-8")
    high_match = re.search(r"^MemoryHigh=(.+)$", text, flags=re.MULTILINE)
    max_match = re.search(r"^MemoryMax=(.+)$", text, flags=re.MULTILINE)
    assert high_match, "MemoryHigh missing from desktop/kurek.service"
    assert max_match, "MemoryMax missing from desktop/kurek.service"

    high_mb = _parse_memory_value(high_match.group(1))
    max_mb = _parse_memory_value(max_match.group(1))

    assert high_mb == 450, f"MemoryHigh should be 450M, got {high_match.group(1)}"
    assert max_mb == 600, f"MemoryMax should be 600M, got {max_match.group(1)}"
    assert high_mb < max_mb
    # Must sit above the documented ~333MB Whisper resident set.
    assert high_mb >= 333
    # Repo unit is a template — installer fills @KUREK_DIR@.
    assert "@KUREK_DIR@" in text
    assert "/home/arch" not in text
    assert "nodaysidle/kurekizmo" not in text
