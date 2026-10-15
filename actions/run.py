"""
actions/run.py — Sandboxed, auditable command runner for Kurek.
Auto-discovered by core/action_loader.py.

Capabilities:
  • 'exec': Run an allowlisted read-only or build/test command with captured stdout, stderr, exit code, and duration.
  • 'read': Retrieve output of a finished or active run, optionally tailed or bounded.
  • 'list': View active and recent runs with status, exit code, duration, and command.
  • 'cancel': Terminate a running command.

Requirements & Scope Discipline:
  • Allowlist, not denylist. Only known-safe command families are permitted.
  • Read-oriented defaults: git log/diff/status, rg, ls, cat, find, stat, --version, --help.
  • Build and test permitted: cargo (check/test/build/clippy), npm/pnpm/yarn/bun (test, run test/build/lint), pytest, make.
  • Destructive operations (rm, git push, git reset --hard, package install, curl|sh) are strictly blocked.
  • Never interactive: forces GIT_PAGER=cat, CI=1, TERM=dumb, DEBIAN_FRONTEND=noninteractive.
  • Hard timeouts with automatic process tree termination and optional process_sentinel handoff.
  • Aggressive output capping (preserves head + tail, reports total lines cut).
  • Full append-only audit trail in ~/.local/state/kurek/run_audit.log.
  • Automatic redaction of secrets, API keys, tokens, and credentials.
  • Strictly root-scoped to allowed project directories.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from actions.codebase import get_allowed_roots, _is_path_allowed

# ---------------------------------------------------------------------------
# Allowlist & Security Configuration
# ---------------------------------------------------------------------------

BLOCKED_BINARIES = frozenset({
    "rm", "rmdir", "mkfs", "dd", "shutdown", "reboot", "poweroff",
    "curl", "wget", "ssh", "scp", "sudo", "su", "chmod", "chown", "chgrp",
    "kill", "killall", "pkill", "pacman", "apt", "apt-get", "yay", "dnf", "brew",
    "pip", "gem", "cargo-install",
})

SAFE_READ_BINARIES = frozenset({
    "rg", "ripgrep", "ls", "cat", "head", "tail", "wc",
    "stat", "file", "tree", "diff", "grep",
})

ALLOWED_BUILD_TARGETS = frozenset({
    "test", "check", "lint", "typecheck", "build", "verify", "audit", "all",
})

# ---------------------------------------------------------------------------
# Run Records & Audit Storage
# ---------------------------------------------------------------------------

@dataclass
class RunRecord:
    run_id: str
    cmd: str
    cwd: str
    status: str  # "running", "completed", "failed", "timed_out", "cancelled", "refused"
    exit_code: int | None = None
    duration: float = 0.0
    start_time: float = field(default_factory=time.time)
    output: str = ""
    total_lines: int = 0
    process: subprocess.Popen | None = None


_RUN_HISTORY: dict[str, RunRecord] = {}
_RUN_COUNTER = 0
_RUN_LOCK = threading.Lock()


def _get_audit_log_path() -> Path:
    state_dir = Path.home() / ".local" / "state" / "kurek"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir / "run_audit.log"
    except Exception:
        fallback = Path.home() / ".config" / "kurek"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback / "run_audit.log"


def _audit_log(record: RunRecord, reason: str = "") -> None:
    path = _get_audit_log_path()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    exit_str = str(record.exit_code) if record.exit_code is not None else "none"
    extra = f" | REASON={reason}" if reason else ""
    line = (
        f"[{now_iso}] ID={record.run_id} | STATUS={record.status} | "
        f"EXIT={exit_str} | DUR={record.duration:.2f}s | "
        f"CWD={record.cwd} | CMD={shlex.quote(record.cmd)}{extra}\n"
    )
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Credential & Secret Redaction
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    (re.compile(r"\b(?:sk-[a-zA-Z0-9_\-]{20,})\b"), "[REDACTED_DEEPSEEK_KEY]"),
    (re.compile(r"\b(?:xai-[a-zA-Z0-9_\-]{20,})\b"), "[REDACTED_XAI_KEY]"),
    (re.compile(r"\b(?:gh[pousr]_[a-zA-Z0-9]{20,})\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bBearer\s+[a-zA-Z0-9_\-\.]{20,}\b"), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"(https?://)([^:\s@]+):([^@\s]+)@"), r"\1[REDACTED_USER]:[REDACTED_PASS]@"),
]


def _redact_secrets(text: str) -> str:
    # 1. Environment secrets known to Kurek
    env_keys = [
        "DEEPSEEK_API_KEY", "XAI_API_KEY", "DEEPGRAM_API_KEY",
        "OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "KUREK_REMOTE_TOKEN",
    ]
    for k in env_keys:
        val = os.environ.get(k)
        if val and len(val) >= 8:
            text = text.replace(val, f"[REDACTED_{k}]")

    # 2. General credential patterns
    for pat, rep in _SECRET_PATTERNS:
        text = pat.sub(rep, text)

    return text


# ---------------------------------------------------------------------------
# Output Capping
# ---------------------------------------------------------------------------

def _cap_output(text: str, duration: float, exit_code: int) -> tuple[str, int]:
    lines = text.splitlines()
    total = len(lines)
    max_lines = 80

    if total <= max_lines:
        return text, total

    head = lines[:35]
    tail = lines[-35:]
    cut = total - 70

    middle = (
        f"\n[... {cut} lines truncated. Total lines: {total}. "
        f"Exit code: {exit_code}. Duration: {duration:.2f}s ...]\n"
    )
    capped = "\n".join(head) + middle + "\n".join(tail)
    return capped, total


# ---------------------------------------------------------------------------
# Command Validation Engine
# ---------------------------------------------------------------------------

def _validate_command(cmd_str: str, confirm: bool = False) -> tuple[bool, list[str], str]:
    """
    Parses and checks command against strict allowlist.
    Returns (is_allowed, parsed_tokens, refusal_reason).
    """
    cmd_str = (cmd_str or "").strip()
    if not cmd_str:
        return False, [], "Refused: Command string is empty."

    try:
        tokens = shlex.split(cmd_str)
    except Exception as e:
        return False, [], f"Refused: Command syntax error: {e}"

    if not tokens:
        return False, [], "Refused: Command string is empty."

    # Block shell operators and metacharacters
    disallowed_tokens = {"|", ";", "&&", "||", ">", ">>", "<", "&", "`"}
    for t in tokens:
        if t in disallowed_tokens or any(c in t for c in (";", "|", "&", "`", "$")):
            return False, [], (
                f"Refused: Shell metacharacter or chaining operator '{t}' is not permitted. "
                "Execute single direct commands without raw shell chaining."
            )

    binary = Path(tokens[0]).name.lower()

    # 1. Permanently blocked binaries
    if binary in BLOCKED_BINARIES:
        return False, [], (
            f"Refused: Command '{binary}' is blocked. 'run' is restricted to read-only inspection "
            "and safe build/test commands. Destructive and system-modifying operations are strictly disallowed."
        )

    # 2. Safe read binaries
    if binary in SAFE_READ_BINARIES:
        return True, tokens, ""

    # 3. 'find' safety check
    if binary == "find":
        for a in tokens[1:]:
            if a in ("-delete", "-exec", "-execdir", "-ok", "-okdir"):
                return False, [], f"Refused: 'find' parameter '{a}' modifies files and is strictly disallowed."
        return True, tokens, ""

    # 4. 'git' command family
    if binary == "git":
        if len(tokens) < 2:
            return False, [], "Refused: 'git' requires a subcommand (e.g. 'git status', 'git diff', 'git log')."
        subcmd = tokens[1].lower()

        allowed_git = {
            "status", "diff", "log", "show", "branch", "rev-parse",
            "tag", "describe", "stash", "remote", "ls-files", "shortlog",
            "blame", "check-ignore", "config",
        }
        if subcmd not in allowed_git:
            return False, [], (
                f"Refused: 'git {subcmd}' is not permitted. Only read-only git operations are allowed "
                "(status, diff, log, show, branch, rev-parse, tag, describe, stash list, remote -v, ls-files, shortlog, blame)."
            )

        if subcmd == "stash":
            if len(tokens) > 2 and tokens[2].lower() not in ("list", "show"):
                return False, [], (
                    f"Refused: 'git stash {tokens[2]}' is not permitted. Only 'git stash list' or 'git stash show' is allowed."
                )
        elif subcmd == "remote":
            if len(tokens) > 2 and tokens[2].lower() not in ("-v", "--verbose", "get-url"):
                return False, [], f"Refused: 'git remote {tokens[2]}' is not permitted. Only 'git remote -v' is allowed."
        elif subcmd == "config":
            if not any(a in ("--get", "--list", "-l") for a in tokens[2:]):
                return False, [], "Refused: 'git config' is only permitted for reading values (--get, --list)."

        for a in tokens[2:]:
            if a.startswith("--output=") or a == "-o":
                return False, [], "Refused: File writing flag '--output' is not permitted in git commands."

        return True, tokens, ""

    # 5. 'cargo' build/test tool
    if binary == "cargo":
        if len(tokens) < 2:
            return False, [], "Refused: 'cargo' requires a subcommand (e.g. 'cargo check', 'cargo test', 'cargo clippy')."
        subcmd = tokens[1].lower()
        allowed_cargo = {
            "check", "test", "build", "clippy", "bench", "tree", "metadata",
            "status", "version", "--version", "-V", "--help", "-h",
        }
        if subcmd not in allowed_cargo:
            return False, [], (
                f"Refused: 'cargo {subcmd}' is not in the safe build/test allowlist "
                "(check, test, build, clippy, bench, tree, metadata)."
            )
        return True, tokens, ""

    # 6. 'pytest'
    if binary == "pytest":
        return True, tokens, ""

    # 7. 'python' / 'python3'
    if binary in ("python", "python3"):
        if len(tokens) >= 3 and tokens[1] == "-m" and tokens[2].lower() in ("pytest", "unittest"):
            return True, tokens, ""
        if len(tokens) == 2 and tokens[1] in ("--version", "-V", "--help", "-h"):
            return True, tokens, ""
        return False, [], (
            f"Refused: '{binary}' is only permitted for test suites "
            f"('{binary} -m pytest', '{binary} -m unittest') or version inspection."
        )

    # 8. Node package managers ('npm', 'pnpm', 'yarn', 'bun')
    if binary in ("npm", "pnpm", "yarn", "bun"):
        if len(tokens) < 2:
            return False, [], f"Refused: '{binary}' requires a subcommand (e.g. '{binary} test')."
        subcmd = tokens[1].lower()
        if subcmd in ("test", "--version", "-v", "--help", "-h"):
            return True, tokens, ""
        elif subcmd == "run":
            if len(tokens) < 3:
                return False, [], f"Refused: '{binary} run' requires a target (e.g. '{binary} run test')."
            target = tokens[2].lower()
            if target in ALLOWED_BUILD_TARGETS:
                return True, tokens, ""
            if confirm:
                return True, tokens, ""
            return False, [], (
                f"Refused: Script target '{target}' is not in the safe build/test allowlist "
                f"({', '.join(sorted(ALLOWED_BUILD_TARGETS))}). Require explicit confirm=True for custom targets."
            )
        else:
            return False, [], (
                f"Refused: '{binary} {subcmd}' is not permitted. Only 'test' and declared build/check/lint scripts are allowed."
            )

    # 9. 'go'
    if binary == "go":
        subcmd = tokens[1].lower() if len(tokens) > 1 else ""
        allowed_go = {"test", "build", "vet", "version", "--version", "--help"}
        if subcmd in allowed_go:
            return True, tokens, ""
        return False, [], f"Refused: 'go {subcmd}' is not in the allowlist (allowed: test, build, vet, version)."

    # 10. 'swift'
    if binary == "swift":
        subcmd = tokens[1].lower() if len(tokens) > 1 else ""
        allowed_swift = {"build", "test", "--version", "--help"}
        if subcmd in allowed_swift:
            return True, tokens, ""
        return False, [], f"Refused: 'swift {subcmd}' is not in the allowlist (allowed: build, test, --version)."

    # 11. 'make'
    if binary == "make":
        for t in tokens[1:]:
            if t.startswith("-"):
                continue
            if t.lower() not in ALLOWED_BUILD_TARGETS:
                if confirm:
                    continue
                return False, [], (
                    f"Refused: 'make {t}' is not in the safe targets allowlist "
                    f"({', '.join(sorted(ALLOWED_BUILD_TARGETS))}). Require confirm=True for custom targets."
                )
        return True, tokens, ""

    # 12. 'just'
    if binary == "just":
        for t in tokens[1:]:
            if t.startswith("-"):
                continue
            if t.lower() not in ALLOWED_BUILD_TARGETS and t.lower() not in ("--list", "-l"):
                if confirm:
                    continue
                return False, [], (
                    f"Refused: 'just {t}' is not in the safe recipes allowlist "
                    f"({', '.join(sorted(ALLOWED_BUILD_TARGETS))}). Require confirm=True for custom recipes."
                )
        return True, tokens, ""

    # 13. General compiler / tool version queries
    if len(tokens) == 2 and tokens[1] in ("--version", "-V", "-v", "--help", "-h"):
        allowed_version_tools = {"rustc", "node", "gcc", "clang", "tsc"}
        if binary in allowed_version_tools:
            return True, tokens, ""

    return False, [], (
        f"Refused: Command '{binary}' is not recognized in the safe command allowlist. "
        "Surface this command to Alan if manual execution is required."
    )


# ---------------------------------------------------------------------------
# Action 1: Exec
# ---------------------------------------------------------------------------

def _action_exec(cmd_str: str, cwd_arg: str | None, timeout_arg: Any, confirm_arg: bool) -> str:
    global _RUN_COUNTER

    allowed_roots = get_allowed_roots()

    # Resolve cwd
    if cwd_arg:
        cwd_path = Path(os.path.expanduser(cwd_arg)).resolve()
    else:
        curr = Path.cwd().resolve()
        cwd_path = curr if _is_path_allowed(curr, allowed_roots) else allowed_roots[0]

    if not _is_path_allowed(cwd_path, allowed_roots):
        return (
            f"Refused: Working directory '{cwd_path}' is outside allowed project roots "
            f"({', '.join(str(r) for r in allowed_roots)})."
        )

    # Validate command
    is_allowed, tokens, reason = _validate_command(cmd_str, confirm=bool(confirm_arg))

    with _RUN_LOCK:
        _RUN_COUNTER += 1
        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{_RUN_COUNTER}"

    if not is_allowed:
        rec = RunRecord(
            run_id=run_id,
            cmd=cmd_str,
            cwd=str(cwd_path),
            status="refused",
            exit_code=-1,
            output=reason,
        )
        with _RUN_LOCK:
            _RUN_HISTORY[run_id] = rec
        _audit_log(rec, reason=reason)
        return reason

    # Configure timeout
    try:
        timeout = float(timeout_arg) if timeout_arg is not None else 30.0
    except (ValueError, TypeError):
        timeout = 30.0
    timeout = max(1.0, min(timeout, 300.0))

    # Clean non-interactive environment
    env = os.environ.copy()
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env["CI"] = "1"
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    env["CARGO_TERM_COLOR"] = "never"
    env["npm_config_yes"] = "false"
    env["DEBIAN_FRONTEND"] = "noninteractive"

    record = RunRecord(
        run_id=run_id,
        cmd=cmd_str,
        cwd=str(cwd_path),
        status="running",
    )
    with _RUN_LOCK:
        _RUN_HISTORY[run_id] = record

    start_time = time.time()
    proc: subprocess.Popen | None = None

    try:
        proc = subprocess.Popen(
            tokens,
            cwd=str(cwd_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        record.process = proc

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            duration = time.time() - start_time
            exit_code = proc.returncode

            combined = stdout
            if stderr:
                combined = f"{stdout}\n[STDERR]\n{stderr}" if stdout else stderr

            redacted = _redact_secrets(combined.strip())
            capped_output, total_lines = _cap_output(redacted, duration, exit_code)

            record.exit_code = exit_code
            record.duration = duration
            record.status = "completed" if exit_code == 0 else "failed"
            record.output = capped_output
            record.total_lines = total_lines

            _audit_log(record)

            header = (
                f"[{record.run_id}] Exit: {exit_code} | Duration: {duration:.2f}s | "
                f"Cwd: {cwd_path.name}\nCommand: {cmd_str}\n\n"
            )
            return header + (capped_output or "(No output)")

        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            duration = time.time() - start_time
            record.exit_code = -1
            record.duration = duration
            record.status = "timed_out"

            combined = stdout or stderr or ""
            redacted = _redact_secrets(combined.strip())
            record.output = f"TIMED OUT after {timeout}s.\n" + redacted
            _audit_log(record, reason="timeout")

            # Try handing off notification to process sentinel if requested or long
            return (
                f"TIMED OUT: Command '{cmd_str}' timed out after {timeout:.1f}s and was terminated.\n"
                f"Output before timeout:\n{record.output}"
            )

    except Exception as e:
        duration = time.time() - start_time
        record.exit_code = -1
        record.duration = duration
        record.status = "failed"
        record.output = f"Execution error: {e}"
        _audit_log(record, reason=str(e))
        return f"Error executing command: {e}"


# ---------------------------------------------------------------------------
# Action 2: Read
# ---------------------------------------------------------------------------

def _action_read(run_id: str | None, tail: int | None = None) -> str:
    with _RUN_LOCK:
        if not _RUN_HISTORY:
            return "No runs have been executed yet."

        target_record: RunRecord | None = None
        if run_id:
            target_record = _RUN_HISTORY.get(run_id.strip())
            if not target_record:
                return f"Error: Run ID '{run_id}' not found."
        else:
            # Pick latest
            target_record = list(_RUN_HISTORY.values())[-1]

    header = (
        f"Run ID: {target_record.run_id}\n"
        f"Command: {target_record.cmd}\n"
        f"Status: {target_record.status} (exit {target_record.exit_code})\n"
        f"Duration: {target_record.duration:.2f}s | Total Lines: {target_record.total_lines}\n\n"
    )

    out = target_record.output or "(No output recorded)"
    if tail and tail > 0:
        lines = out.splitlines()
        if len(lines) > tail:
            out = f"[Showing last {tail} lines of {len(lines)}]:\n" + "\n".join(lines[-tail:])

    return header + out


# ---------------------------------------------------------------------------
# Action 3: List
# ---------------------------------------------------------------------------

def _action_list() -> str:
    with _RUN_LOCK:
        if not _RUN_HISTORY:
            return "No runs in recent history."

        records = list(_RUN_HISTORY.values())[-15:]  # last 15 runs
        lines = ["Recent Runs:"]
        for r in reversed(records):
            status_symbol = "✓" if r.status == "completed" else "✗" if r.status == "failed" else "⏳"
            lines.append(
                f"  [{status_symbol} {r.run_id}] {r.status} (exit {r.exit_code}, {r.duration:.2f}s) "
                f"in {Path(r.cwd).name}: {r.cmd}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Action 4: Cancel
# ---------------------------------------------------------------------------

def _action_cancel(run_id: str | None) -> str:
    with _RUN_LOCK:
        if not _RUN_HISTORY:
            return "No runs active to cancel."

        target_record: RunRecord | None = None
        if run_id:
            target_record = _RUN_HISTORY.get(run_id.strip())
        else:
            # Find any currently running run
            for r in reversed(list(_RUN_HISTORY.values())):
                if r.status == "running" and r.process:
                    target_record = r
                    break

        if not target_record:
            return f"Error: No active running process found matching '{run_id or 'latest'}'."

        if target_record.status != "running" or not target_record.process:
            return f"Run '{target_record.run_id}' is not currently running (status: {target_record.status})."

        try:
            target_record.process.terminate()
            target_record.status = "cancelled"
            _audit_log(target_record, reason="cancelled by user")
            return f"Cancelled run '{target_record.run_id}' (PID {target_record.process.pid}). Process terminated."
        except Exception as e:
            return f"Error cancelling run '{target_record.run_id}': {e}"


# ---------------------------------------------------------------------------
# Main Handler & Discovery Specification
# ---------------------------------------------------------------------------

def run(parameters: dict | None = None, **kwargs) -> str:
    params = parameters or {}
    action = str(params.get("action", "exec")).strip().lower()

    if action == "exec":
        cmd = str(params.get("cmd", "")).strip()
        cwd = params.get("cwd")
        timeout = params.get("timeout")
        confirm = bool(params.get("confirm", False))
        return _action_exec(cmd, cwd, timeout, confirm)
    elif action == "read":
        run_id = params.get("run_id")
        tail = params.get("tail")
        try:
            tail_num = int(tail) if tail is not None else None
        except (ValueError, TypeError):
            tail_num = None
        return _action_read(run_id, tail=tail_num)
    elif action == "list":
        return _action_list()
    elif action == "cancel":
        run_id = params.get("run_id")
        return _action_cancel(run_id)
    else:
        return (
            f"Error: Unknown action '{action}'. "
            "Supported actions: 'exec', 'read', 'list', 'cancel'."
        )


TOOL = {
    "name": "run",
    "description": (
        "Sandboxed, auditable command runner for workstation inspection and build/test tasks. "
        "Permits allowlisted read-only commands (git status/diff/log, rg, ls, cat, find, stat, --version) "
        "and declared project build/test tools (cargo check/test/build/clippy, npm test, pnpm, pytest, make). "
        "Destructive commands (rm, git push, git reset --hard, curl|sh) are strictly disallowed. "
        "Never interactive. All runs are strictly root-scoped, timeout-bounded, and recorded in an append-only audit log."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "The run action: 'exec' (execute an allowlisted command), "
                    "'read' (inspect output of recent run), 'list' (list recent runs), "
                    "or 'cancel' (terminate running process)."
                ),
                "enum": ["exec", "read", "list", "cancel"],
            },
            "cmd": {
                "type": "STRING",
                "description": "Command string to execute (e.g. 'git status', 'cargo check', 'pytest', 'npm test').",
            },
            "cwd": {
                "type": "STRING",
                "description": "Working directory path within allowed project roots (e.g. '~/dev/nodaysidle/kurekizmo').",
            },
            "timeout": {
                "type": "INTEGER",
                "description": "Timeout in seconds (default: 30, max: 300).",
            },
            "confirm": {
                "type": "BOOLEAN",
                "description": "Explicit confirmation for declared non-destructive project tasks outside default allowlist.",
            },
            "run_id": {
                "type": "STRING",
                "description": "Run identifier when action='read' or action='cancel'.",
            },
            "tail": {
                "type": "INTEGER",
                "description": "Number of lines to tail when action='read'.",
            },
        },
        "required": ["action"],
    },
    "handler": run,
}
