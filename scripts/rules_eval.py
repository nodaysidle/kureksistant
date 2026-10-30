#!/usr/bin/env python3
"""Does Kurek follow its rules? Run adversarial prompts through the real prompt and score the replies.

    .venv/bin/python scripts/rules_eval.py [--reps 3] [--no-control] [--out FILE]

Each case in scripts/rules_eval.toml (plus your own in ~/.config/kurek/rules_eval.toml, or
KUREK_RULES_EVAL_FILE) is run with your rules.toml loaded and, as a control, with no rules.
Scoring is plain code on the reply text and the tools called, never a model's opinion.
Desktop tools are recorded, not executed (see llm_ab.py); read-only tools really run.

Case fields: id, rule, prompt, and any of max_words, max_sentences, must_match, must_not_match
(regexes, case-insensitive), must_call, must_not_call (tool names).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

DEFAULT_CASES = HERE / "rules_eval.toml"
LOCAL_CASES = Path(os.environ.get("KUREK_RULES_EVAL_FILE") or Path.home() / ".config" / "kurek" / "rules_eval.toml")


def check(case: dict, reply: str, tools: list[str]) -> list[str]:
    """Why the reply breaks the case's expectations; an empty list means it passed."""
    failures = []
    # "1. " and "**1." list markers end in a period but are not sentences
    prose = re.sub(r"(?m)^\s*\**\d+\.\s*", "", reply.strip())
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", prose) if s]
    if "max_words" in case and len(reply.split()) > case["max_words"]:
        failures.append(f"{len(reply.split())} words, limit {case['max_words']}")
    if "max_sentences" in case and len(sentences) > case["max_sentences"]:
        failures.append(f"{len(sentences)} sentences, limit {case['max_sentences']}")
    if case.get("must_match") and not re.search(case["must_match"], reply, re.I):
        failures.append(f"missing /{case['must_match']}/")
    if case.get("must_not_match") and (hit := re.search(case["must_not_match"], reply, re.I)):
        failures.append(f"contains '{hit.group(0)}'")
    called = {t.split("(")[0] for t in tools}
    if case.get("must_call") and not called & set(case["must_call"]):
        failures.append(f"did not call {case['must_call']}")
    if case.get("must_not_call") and (bad := called & set(case["must_not_call"])):
        failures.append(f"called {sorted(bad)}")
    return failures


def load_cases(*paths: Path) -> list[dict]:
    cases: list[dict] = []
    for path in paths:
        if path.exists():
            cases += tomllib.loads(path.read_text(encoding="utf-8")).get("case", [])
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise SystemExit(f"duplicate case ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    return cases


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--no-control", action="store_true", help="skip the no-rules control run")
    ap.add_argument("--only", help="run only the case with this id")
    ap.add_argument("--out", default="rules_eval.json")
    args = ap.parse_args()

    cases = [c for c in load_cases(DEFAULT_CASES, LOCAL_CASES) if not args.only or c["id"] == args.only]
    from llm_ab import build_engine, run_one

    import core.rules as rules
    real_rules_path = rules.RULES_PATH
    engine = build_engine()
    conditions = [("rules on", real_rules_path)] + ([] if args.no_control else [("rules off", Path("/nonexistent/rules.toml"))])

    results = []
    for case in cases:
        row = {"id": case["id"], "rule": case.get("rule", ""), "prompt": case["prompt"], "runs": {}}
        for label, path in conditions:
            rules.RULES_PATH = path
            runs = []
            for _ in range(args.reps):
                rec = run_one(engine, "haiku", case["prompt"])
                fails = check(case, rec["reply"], rec["tools"])
                runs.append({"reply": rec["reply"], "tools": rec["tools"], "failures": fails,
                             "first_sentence_s": rec["first_sentence_s"], "total_s": rec["total_s"]})
            row["runs"][label] = runs
            passed = sum(not r["failures"] for r in runs)
            print(f"{case['id']:24} {label:9} {passed}/{len(runs)}  {runs[0]['failures'] or ''}", flush=True)
        results.append(row)
    rules.RULES_PATH = real_rules_path

    Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{'case':24} " + "  ".join(f"{label:>9}" for label, _ in conditions))
    totals = {label: [0, 0] for label, _ in conditions}
    for row in results:
        cells = []
        for label, _ in conditions:
            runs = row["runs"][label]
            ok = sum(not r["failures"] for r in runs)
            totals[label][0] += ok
            totals[label][1] += len(runs)
            cells.append(f"{ok}/{len(runs)}".rjust(9))
        print(f"{row['id']:24} " + "  ".join(cells))
    print(f"{'TOTAL':24} " + "  ".join(f"{totals[l][0]}/{totals[l][1]}".rjust(9) for l, _ in conditions))
    for label, _ in conditions:
        times = [r["total_s"] for row in results for r in row["runs"][label]]
        print(f"mean reply time, {label}: {sum(times) / len(times):.1f}s over {len(times)} runs")
    print(f"wrote {args.out}")
    return 0 if totals["rules on"][0] == totals["rules on"][1] else 1


if __name__ == "__main__":
    sys.exit(main())
