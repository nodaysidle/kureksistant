import hashlib
from pathlib import Path

import pytest

from core.rules import RULES_UNAVAILABLE, RulesError, load_rules, pinned_block, prompt_block


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "rules.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_missing_file_is_empty_ruleset(tmp_path: Path) -> None:
    rs = load_rules(tmp_path / "nope.toml")
    assert rs.rules == ()
    assert rs.render() == ""


def test_rule_text_is_verbatim(tmp_path: Path) -> None:
    text = "Never  push to main —  “ever”, ok?  "
    p = _write(tmp_path, f'[[rule]]\nid = "a"\ntext = {text!r}\n'.replace("'", '"'))
    rs = load_rules(p)
    assert rs.rules[0].text == text
    assert rs.render() == f"- [a] {text}"


def test_order_and_hash_follow_file_bytes(tmp_path: Path) -> None:
    body = '[[rule]]\nid = "b"\ntext = "two"\n\n[[rule]]\nid = "a"\ntext = "one"\n'
    p = _write(tmp_path, body)
    rs = load_rules(p)
    assert [r.id for r in rs.rules] == ["b", "a"]
    assert rs.sha256 == hashlib.sha256(body.encode()).hexdigest()


@pytest.mark.parametrize(
    "body",
    [
        "[[rule]\nid =",
        '[[rule]]\ntext = "no id"\n',
        '[[rule]]\nid = "x"\ntext = "   "\n',
        '[[rule]]\nid = "x"\ntext = "a"\n[[rule]]\nid = "x"\ntext = "b"\n',
        'rule = "not a table array"\n',
    ],
)
def test_malformed_file_raises(tmp_path: Path, body: str) -> None:
    with pytest.raises(RulesError):
        load_rules(_write(tmp_path, body))


def test_prompt_block_empty_when_no_rules(tmp_path: Path) -> None:
    assert prompt_block(tmp_path / "nope.toml") == ""


def test_prompt_block_carries_rule_text(tmp_path: Path) -> None:
    p = _write(tmp_path, '[[rule]]\nid = "a"\ntext = "never push"\n')
    block = prompt_block(p)
    assert block.startswith("[USER RULES]")
    assert block.endswith("- [a] never push")


def test_prompt_block_flags_unreadable_file(tmp_path: Path) -> None:
    assert prompt_block(_write(tmp_path, "[[rule]\n")) == RULES_UNAVAILABLE


def test_pin_defaults_to_false_and_reads_true(tmp_path: Path) -> None:
    p = _write(tmp_path, '[[rule]]\nid = "a"\ntext = "x"\n[[rule]]\nid = "b"\ntext = "y"\npin = true\n')
    assert [r.pin for r in load_rules(p).rules] == [False, True]


def test_non_boolean_pin_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RulesError, match="pin"):
        load_rules(_write(tmp_path, '[[rule]]\nid = "a"\ntext = "x"\npin = "yes"\n'))


def test_pinned_block_carries_only_pinned_rules_verbatim(tmp_path: Path) -> None:
    text = "Keep  replies — short."
    p = _write(tmp_path, f'[[rule]]\nid = "a"\ntext = "other"\n[[rule]]\nid = "b"\ntext = "{text}"\npin = true\n')
    block = pinned_block(p)
    assert block.endswith(f"- [b] {text}") and "other" not in block


def test_pinned_block_is_empty_without_pins_or_on_a_bad_file(tmp_path: Path) -> None:
    assert pinned_block(_write(tmp_path, '[[rule]]\nid = "a"\ntext = "x"\n')) == ""
    assert pinned_block(_write(tmp_path, "[[rule]\n")) == ""
    assert pinned_block(tmp_path / "nope.toml") == ""
