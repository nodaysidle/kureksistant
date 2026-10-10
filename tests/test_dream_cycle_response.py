"""Dream cycle must accept str | dict | None from query_deepseek without AttributeError."""
from __future__ import annotations

from pathlib import Path

import core.dream_cycle as dream_cycle
from core.dream_cycle import text_from_llm_response


def test_text_from_llm_response_normalises_shapes() -> None:
    assert text_from_llm_response("  hello  ") == "hello"
    assert text_from_llm_response({"content": "from dict"}) == "from dict"
    assert text_from_llm_response({"content": None}, default="fallback") == "fallback"
    assert text_from_llm_response(None, default="quiet") == "quiet"
    assert text_from_llm_response(None) == ""


def test_execute_dream_cycle_handles_str_dict_none(monkeypatch, tmp_path: Path) -> None:
    dreams = tmp_path / "dreams"
    memory = tmp_path / "memory"
    dreams.mkdir()
    memory.mkdir()
    (memory / "today.md").write_text("User asked about Rust.\n", encoding="utf-8")

    monkeypatch.setattr(dream_cycle, "HOME", tmp_path)
    monkeypatch.setattr(dream_cycle, "DREAMS_DIR", dreams)
    monkeypatch.setattr(dream_cycle, "MEMORY_DIR", memory)
    monkeypatch.setattr(dream_cycle, "ALIGNMENT_FILE", tmp_path / "ALIGNMENT_SYNTHESIS.md")
    monkeypatch.setattr(dream_cycle, "STATE_FILE", tmp_path / "ALIGNMENT_STATE.yaml")
    monkeypatch.setattr(dream_cycle, "REPAIRS_FILE", tmp_path / "REPAIR_THREADS.yaml")

    # Force today's daily file path used inside execute_dream_cycle.
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    (memory / f"{today}.md").write_text("Built the streaming TTS path.\n", encoding="utf-8")

    responses = iter(
        [
            "Atmospheric dream prose about the workstation.",
            {"content": "- Prefer concise replies\n"},
        ]
    )

    def fake_query_deepseek(**_kwargs):
        return next(responses)

    monkeypatch.setattr("core.llm_client.query_deepseek", fake_query_deepseek)

    result = dream_cycle.execute_dream_cycle(force=True)
    assert result["status"] == "success"
    dream_text = (dreams / f"{today}.md").read_text(encoding="utf-8")
    assert "Atmospheric dream prose" in dream_text
    align_text = (tmp_path / "ALIGNMENT_SYNTHESIS.md").read_text(encoding="utf-8")
    assert "Prefer concise replies" in align_text


def test_execute_dream_cycle_align_none_no_attribute_error(monkeypatch, tmp_path: Path) -> None:
    dreams = tmp_path / "dreams"
    memory = tmp_path / "memory"
    dreams.mkdir()
    memory.mkdir()

    monkeypatch.setattr(dream_cycle, "HOME", tmp_path)
    monkeypatch.setattr(dream_cycle, "DREAMS_DIR", dreams)
    monkeypatch.setattr(dream_cycle, "MEMORY_DIR", memory)
    monkeypatch.setattr(dream_cycle, "ALIGNMENT_FILE", tmp_path / "ALIGNMENT_SYNTHESIS.md")
    monkeypatch.setattr(dream_cycle, "STATE_FILE", tmp_path / "ALIGNMENT_STATE.yaml")
    monkeypatch.setattr(dream_cycle, "REPAIRS_FILE", tmp_path / "REPAIR_THREADS.yaml")

    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    (memory / f"{today}.md").write_text("Quiet maintenance day.\n", encoding="utf-8")

    responses = iter(["Dream text from string return.", None])

    def fake_query_deepseek(**_kwargs):
        return next(responses)

    monkeypatch.setattr("core.llm_client.query_deepseek", fake_query_deepseek)

    result = dream_cycle.execute_dream_cycle(force=True)
    assert result["status"] == "success"
    # None alignment guidance must not write ALIGNMENT_SYNTHESIS.md
    assert not (tmp_path / "ALIGNMENT_SYNTHESIS.md").exists()
