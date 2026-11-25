# AGENTS.md — Kurek (Kureksistant)

- Full self-check: `bash scripts/verify.sh` (runs an isolated copy on a throwaway port; never touches the live DB, socket or port 8790).
- Tests and ad-hoc scripts never open the live ledger (`$XDG_DATA_HOME/kurek/kurek_memory.db`) or bind the live port 8790; use temp databases and throwaway ports (e.g. 18791). Export `OPENBLAS_NUM_THREADS=1` before importing `kurek_daemon` in tests (OpenBLAS / PortAudio dlopen deadlock).
- Kurek is headless by design: do not introduce GUI libraries.

## Agent skills

### Issue tracker

GitHub Issues on nodaysidle/kureksistant, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five labels: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `docs/GLOSSARY.md` + `docs/adr/` (created lazily). See `docs/agents/domain.md`.
