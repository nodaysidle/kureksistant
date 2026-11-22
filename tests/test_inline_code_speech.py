"""C8: inline `code` is spoken without its backticks; fenced blocks and URLs stay out of speech."""
from __future__ import annotations

import kurek_daemon as daemon

S = daemon.KurekEngine.sanitize_text_for_speech


def test_inline_code_keeps_its_words() -> None:
    assert S("Run `kurek doctor` to fix it.") == "Run kurek doctor to fix it."


def test_fenced_code_block_is_still_dropped() -> None:
    assert "rm" not in S("See:\n```\nrm -rf x\n```\nDone.")


def test_url_inside_backticks_is_still_dropped() -> None:
    spoken = S("Open `https://example.com/a` now.")
    assert "https" not in spoken
    assert "example.com" not in spoken
