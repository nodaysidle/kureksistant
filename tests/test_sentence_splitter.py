"""Unit tests for streaming sentence boundaries and abbreviation handling."""
from __future__ import annotations

from core.llm_client import flush_sentence_buffer, pop_complete_sentence


def test_pop_complete_sentence_basic() -> None:
    sentence, rest = pop_complete_sentence("Hello there. More text")
    assert sentence == "Hello there."
    assert rest == "More text"


def test_abbreviations_do_not_split() -> None:
    buf = "Dr. Smith arrived. Later we left."
    sentence, rest = pop_complete_sentence(buf)
    assert sentence == "Dr. Smith arrived."
    assert rest == "Later we left."

    for sample in (
        "Mr. Jones called. Done now.",
        "Mrs. Lee waved. All good.",
        "Ms. Park agreed. Next steps.",
        "See e.g. the docs. Then stop.",
        "That is i.e. correct. Move on.",
        "Bring snacks etc. tomorrow. End.",
        "Rust vs. Go debate. Settled.",
        "Meet at St. Mary soon. Bye.",
    ):
        sentence, rest = pop_complete_sentence(sample)
        assert sentence is not None, sample
        # Abbreviation period must remain inside the first sentence.
        assert rest  # there is a second sentence
        assert not rest.lower().startswith(
            ("smith", "jones", "lee", "park", "the docs", "correct", "tomorrow", "go", "mary")
        )


def test_decimals_do_not_split() -> None:
    sentence, rest = pop_complete_sentence("Version 3.5 shipped today. Notes follow.")
    assert sentence == "Version 3.5 shipped today."
    assert rest == "Notes follow."


def test_flush_short_trailing_sentence() -> None:
    # Held back mid-stream because < 2 words after a boundary.
    assert flush_sentence_buffer("OK.") == "OK."
    assert flush_sentence_buffer("  almost") == "almost"
    assert flush_sentence_buffer("   ") is None


def test_stream_end_flushes_short_fragment() -> None:
    """Simulate held-back short text then end-of-stream flush."""
    sentence, rest = pop_complete_sentence("Yes. ")
    # "Yes." is only one word → not popped mid-stream.
    assert sentence is None
    assert flush_sentence_buffer(rest if rest else "Yes. ") == "Yes."
