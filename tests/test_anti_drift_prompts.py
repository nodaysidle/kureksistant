"""Anti-drift guard (docs/ANTI-DRIFT.md): reply length and language live only in the
user's rules.toml, and no code writes that file."""
from __future__ import annotations

import re
from pathlib import Path

import kurek_daemon as daemon
from tests.test_stream_errors import _make_lite_engine

ROOT = Path(__file__).resolve().parents[1]

# A number of sentences or words, e.g. "1 to 15 sentences", "under 300 words", "4 sentences maximum".
LENGTH_LIMIT = re.compile(r"\b\d+\s*(?:to|-|–)?\s*\d*\s+(?:spoken\s+)?(?:sentences|words)\b", re.I)
LANGUAGE_POLICY = re.compile(r"reply in (?:english|slovenian|italian)\b|\b(?:english|language)[- ]only\b", re.I)


def _system_prompt(monkeypatch, tmp_path) -> str:
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"
    monkeypatch.setattr(daemon, "rules_prompt_block", lambda: "")
    monkeypatch.setattr(daemon, "rules_pinned_block", lambda: "")
    seen: list[list[dict]] = []

    def fake_stream(**kwargs):
        seen.append(kwargs["messages"])
        yield {"type": "done", "full_content": "ok"}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    engine.handle_text_query("hello")
    return seen[0][0]["content"]


def test_system_prompt_defers_length_and_language_to_user_rules(monkeypatch, tmp_path):
    prompt = _system_prompt(monkeypatch, tmp_path)
    assert "[USER RULES]" in prompt
    assert not LENGTH_LIMIT.search(prompt), LENGTH_LIMIT.search(prompt).group(0)
    assert not LANGUAGE_POLICY.search(prompt), LANGUAGE_POLICY.search(prompt).group(0)


def test_no_second_copy_of_the_prompt():
    # core/prompt.txt was a copy nothing loaded; it drifted from the real prompt.
    assert not (ROOT / "core" / "prompt.txt").exists()


def test_only_core_rules_touches_the_rules_file_and_it_never_writes():
    for py in [ROOT / "kurek_daemon.py", *ROOT.glob("core/*.py"), *ROOT.glob("actions/*.py"), *ROOT.glob("memory/*.py")]:
        if py.name == "rules.py":
            continue
        assert "RULES_PATH" not in py.read_text(encoding="utf-8"), py
    src = (ROOT / "core" / "rules.py").read_text(encoding="utf-8")
    for call in ("write_text", "write_bytes", "open(", ".unlink", "os.replace", "shutil."):
        assert call not in src, call
