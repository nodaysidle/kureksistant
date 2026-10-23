import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rules_eval.py"
spec = importlib.util.spec_from_file_location("rules_eval", SCRIPT)
rules_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules_eval)
check = rules_eval.check


def test_a_reply_inside_every_limit_passes() -> None:
    case = {"max_words": 10, "max_sentences": 2, "must_match": "foot", "must_not_match": "sorry"}
    assert check(case, "You use foot. It is your default.", []) == []


@pytest.mark.parametrize(
    "case,reply,tools,needle",
    [
        ({"max_words": 3}, "one two three four", [], "4 words"),
        ({"max_sentences": 1}, "One. Two.", [], "2 sentences"),
        ({"must_match": "foot"}, "kitty", [], "missing"),
        ({"must_not_match": "sorry|apolog"}, "Sorry about that.", [], "contains 'Sorry'"),
        ({"must_call": ["web_search"]}, "It was Max.", [], "did not call"),
        ({"must_call": ["web_search"]}, "It was Max.", ["weather_report({})"], "did not call"),
        ({"must_not_call": ["file_controller"]}, "Done.", ['file_controller({"action": "delete"})'], "called"),
    ],
)
def test_each_expectation_reports_why_it_failed(case, reply, tools, needle) -> None:
    failures = check(case, reply, tools)
    assert len(failures) == 1 and needle in failures[0]


def test_tool_names_ignore_arguments() -> None:
    assert check({"must_call": ["web_search"]}, "ok", ['web_search({"query": "f1"})']) == []


def test_shipped_cases_load_with_unique_ids_and_only_known_fields() -> None:
    cases = rules_eval.load_cases(rules_eval.DEFAULT_CASES)
    known = {"id", "rule", "prompt", "max_words", "max_sentences", "must_match", "must_not_match", "must_call", "must_not_call"}
    assert len(cases) >= 8
    assert all({"id", "prompt"} <= set(c) and set(c) <= known for c in cases)


def test_duplicate_case_ids_are_refused(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    f.write_text('[[case]]\nid = "a"\nprompt = "x"\n[[case]]\nid = "a"\nprompt = "y"\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        rules_eval.load_cases(f)
