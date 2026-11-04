---
title: "CSV and TSV reading in file_processor went unnoticed because pandas is not a dependency, so its test skips"
author: Nodaysidle
date: 2026-11-04
branch: fix/file-processor-read-csv
cost: { minutes: 2, tokens: 111596 }
id: l-b674db70
scope: repo
status: active
src: agent
touches:
  - actions/file_processor.py
  - tests/test_actions_hardening.py
  - requirements.txt
---

## Decided
2026-11-04, issue #34: fix the keyword (`encoding_errors`) and split `.tsv` on tabs. Did not add pandas to requirements; it is deliberately optional (see requirements.txt comment).

## Tried and ruled out
Adding pandas as a dependency just to run the test: heavy for a daemon with a 600M memory cap.

## Watch out
pandas is not in `.venv`, so the CSV test skips (`importorskip`) in CI and locally. A broken data path can ship unnoticed. To really run it: `uv pip install --target <tmpdir> pandas` and set `PYTHONPATH`.

In the code: actions/file_processor.py `_process_data`; tests/test_actions_hardening.py `test_process_data_reads_csv_and_tsv`.
