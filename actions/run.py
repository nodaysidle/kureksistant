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
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from actions.codebase import get_allowed_roots, _is_path_allowed, is_secret_file, git_safe_env, _rg_env
from core import confirm as _confirm

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
    status: str  # "running", "completed", "failed", "timed_out", "cancelled", "refused", "awaiting_confirmation"
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
    # Provider-specific prefixes first so each key gets the right label.
    (re.compile(r"\bsk-ant-[a-zA-Z0-9_\-]{20,}"), "[REDACTED_ANTHROPIC_KEY]"),
    (re.compile(r"\bsk-proj-[a-zA-Z0-9_\-]{20,}"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"\bsk-or-[a-zA-Z0-9_\-]{20,}"), "[REDACTED_OPENROUTER_KEY]"),
    (re.compile(r"\bsk-[a-zA-Z0-9_\-]{20,}"), "[REDACTED_SK_KEY]"),
    (re.compile(r"\bxai-[a-zA-Z0-9_\-]{20,}"), "[REDACTED_XAI_KEY]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"\btskey-[a-zA-Z0-9_\-]{8,}"), "[REDACTED_TAILSCALE_KEY]"),
    (re.compile(r"\b(?:gh[pousr]_[a-zA-Z0-9]{20,}|github_pat_[a-zA-Z0-9_]{20,})"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bBearer\s+[a-zA-Z0-9_\-\.=]{20,}"), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"(https?://)([^:\s@/]+):([^@\s]+)@"), r"\1[REDACTED_USER]:[REDACTED_PASS]@"),
    # Generic key/value assignments: KEY=value, "api_key": "value", token: value
    (re.compile(
        r"""(?i)(["']?[A-Za-z0-9_\-]*(?:api[_\-]?key|apikey|secret|token|passw(?:or)?d|auth[_\-]?key|private[_\-]?key)["']?\s*[:=]\s*["']?)"""
        r"""(?!\[REDACTED)(?=[A-Za-z0-9_\-+/=.~]*\d)([A-Za-z0-9_\-+/=.~]{8,})(?![\w(])"""
    ), r"\1[REDACTED_SECRET]"),
    # Quoted credential values (JSON/YAML/TOML/code literals): redact regardless of digits.
    (re.compile(
        r"""(?i)(["']?[A-Za-z0-9_\-]*(?:api[_\-]?key|apikey|secret|token|passw(?:or)?d|auth[_\-]?key|private[_\-]?key)["']?\s*[:=]\s*["'])"""
        r"""(?!\[REDACTED)([^"'\s]{8,})(?=["'])"""
    ), r"\1[REDACTED_SECRET]"),
]

# Environment variables whose values are always scrubbed from output.
_SENSITIVE_ENV_EXACT = frozenset({
    "DEEPSEEK_API_KEY", "XAI_API_KEY", "DEEPGRAM_API_KEY", "OPENROUTER_API_KEY",
    "TYPESAFE_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY", "KUREK_REMOTE_TOKEN", "TS_AUTHKEY", "TAILSCALE_AUTHKEY",
    "GITHUB_TOKEN", "GH_TOKEN",
})
_SENSITIVE_ENV_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_AUTHKEY", "_PASSWORD")


def _is_sensitive_env(name: str) -> bool:
    return (
        name in _SENSITIVE_ENV_EXACT
        or name.startswith("KUREK_REMOTE_")
        or name.endswith(_SENSITIVE_ENV_SUFFIXES)
    )


def _redact_secrets(text: str) -> str:
    # 1. Environment secrets known to Kurek (longest first so substrings don't leak)
    env_items = sorted(
        ((k, v) for k, v in os.environ.items() if _is_sensitive_env(k) and v and len(v) >= 8),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )
    for k, val in env_items:
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

# Flags that make 'rg' execute external programs (finding: rg --pre=rm).
_RG_EXEC_FLAGS = frozenset({"--pre", "--pre-glob", "--hostname-bin", "--search-zip", "-z"})

# find predicates that execute, delete, or write files.
_FIND_BLOCKED = frozenset({
    "-delete", "-exec", "-execdir", "-ok", "-okdir",
    "-fprint", "-fprint0", "-fprintf", "-fls",
})

# make: allowlist of options (anything else, e.g. -f/--eval/-C/-I/-E, is refused).
_MAKE_SAFE_FLAGS = frozenset({
    "-n", "--dry-run", "--just-print", "--recon", "-k", "--keep-going", "-s", "--silent",
    "--quiet", "-B", "--always-make", "--no-print-directory", "-w", "--print-directory",
    "-q", "--question", "-S", "--no-keep-going", "--stop", "-r", "--no-builtin-rules",
    "-R", "--no-builtin-variables", "--warn-undefined-variables",
})
_MAKE_J_RE = re.compile(r"^(?:-j\d*|--jobs(?:=\d+)?|-l\d*(?:\.\d+)?|--load-average(?:=\d+(?:\.\d+)?)?)$")

# just: allowlist of options (blocks --justfile/-f, --working-directory/-d, --shell, --set, ...).
_JUST_SAFE_FLAGS = frozenset({"--list", "-l", "--dry-run", "-n", "--summary", "--quiet", "-q"})

# cargo: options that can rewrite config, toolchain wrappers, or write outside the repo.
_CARGO_BLOCKED_FLAGS = frozenset({"--config", "--target-dir", "--manifest-path", "--artifact-dir", "--out-dir"})

# npm/pnpm/yarn/bun: options that swap the script shell, config, or working directory.
_NODE_PM_BLOCKED_FLAGS = frozenset({
    "--script-shell", "--prefix", "-C", "--dir", "--cwd", "--userconfig", "--globalconfig",
    "--node-options", "--shell", "--config", "-c", "--workspace-root", "--filter",
})

# go: options that execute a custom program.
_GO_BLOCKED_FLAGS = frozenset({"exec", "toolexec", "overlay", "modfile", "o", "pgo", "C"})

# git branch/tag: read-only listing only.
_GIT_REF_MUTATING = {
    "branch": frozenset({
        "-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy", "-f", "--force",
        "--edit-description", "-u", "--set-upstream-to", "--unset-upstream", "--create-reflog",
        "-t", "--track", "--no-track", "--recurse-submodules",
    }),
    "tag": frozenset({
        "-d", "--delete", "-a", "--annotate", "-s", "--sign", "-m", "--message", "-F", "--file",
        "-f", "--force", "-u", "--local-user", "--create-reflog", "--cleanup", "-e", "--edit",
        "--trailer",
    }),
}
# Flags after which ONE positional value (pattern/commit/format) is a read argument.
_GIT_REF_VALUE_FLAGS = frozenset({
    "--list", "-l", "--contains", "--no-contains", "--merged", "--no-merged",
    "--points-at", "--format", "--sort", "--color", "--column",
})
_GIT_SECRET_CONFIG_KEY = re.compile(
    r"(?i)^(credential\.|http\..*extraheader|url\..*\.insteadof|.*(token|password|passwd|secret|apikey|api-key|api_key)$)"
)
_GIT_CONFIG_WRITE_FLAGS = frozenset({
    "--add", "--unset", "--unset-all", "--replace-all", "--edit", "-e", "--rename-section",
    "--remove-section", "set", "unset", "rename-section", "remove-section", "edit",
    "-f", "--file", "--blob", "--worktree", "--system",
})

# Binaries whose non-flag arguments are file paths and must stay inside the allowed roots.
def _flag_base(tok: str) -> str:
    return tok.split("=", 1)[0]


def _validate_git_ref_args(subcmd: str, args: list[str]) -> str:
    """Allow only read-only listing forms of 'git branch' / 'git tag'."""
    mutating = _GIT_REF_MUTATING[subcmd]
    prev: str | None = None
    for a in args:
        if a.startswith("-"):
            base = _flag_base(a)
            if base in mutating or (
                len(a) > 2 and not a.startswith("--") and any(f"-{c}" in mutating for c in a[1:])
            ):
                return f"Refused: 'git {subcmd} {a}' modifies refs. Only read-only listing is permitted."
            prev = base if "=" not in a else None
            continue
        if prev in _GIT_REF_VALUE_FLAGS:
            prev = None
            continue
        return (
            f"Refused: 'git {subcmd} {a}' would create or change a ref. "
            f"Only read-only listing is permitted (e.g. 'git {subcmd} --list')."
        )
    return ""


def _path_candidates(tokens: list[str]) -> list[tuple[str, str]]:
    """(original token, path-like value) pairs: positionals, --flag=VALUE and attached -fVALUE."""
    out: list[tuple[str, str]] = []
    for tok in tokens[1:]:
        if tok == "-" or tok == "--":
            continue
        if tok.startswith("--"):
            if "=" in tok:
                out.append((tok, tok.split("=", 1)[1]))
            continue
        if tok.startswith("-"):
            if len(tok) > 2:
                out.append((tok, tok[2:]))
            continue
        out.append((tok, tok))
    return out


def _validate_path_args(binary: str, tokens: list[str], base_dir: Path, allowed_roots: list[Path]) -> str:
    """Refuse arguments (any allowlisted tool) that point at existing paths outside the roots or at secret files."""
    if binary == "git":
        # git can read secrets that are not on disk: 'HEAD:.env', 'log -p -- .env', '-- config/api_keys.json'.
        for tok, value in _path_candidates(tokens[1:]):
            if value.startswith(":") and len(value) > 1 and value[1] in "(/!^":
                return (
                    f"Refused: git pathspec magic '{tok}' is not permitted; "
                    "pass a plain relative path instead."
                )
            # 'rev:path', ':path', ':0:path' .. ':3:path' -> the path is after the last ':'
            spec = value.rsplit(":", 1)[-1] if ":" in value else value
            if spec and is_secret_file(Path(spec)):
                return (
                    f"Refused: git argument '{tok}' targets a credential/secret file "
                    "(.env*, api_keys.json, *.pem, id_* keys, .git/*)."
                )
    for tok, value in _path_candidates(tokens):
        if not value:
            continue
        try:
            cand = Path(os.path.expanduser(value))
            if not cand.is_absolute():
                cand = base_dir / cand
            resolved = cand.resolve()
        except Exception:
            continue
        if not (resolved.exists() or cand.is_symlink()):
            continue  # not a path (e.g. a search pattern)
        if not _is_path_allowed(resolved, allowed_roots):
            return (
                f"Refused: Argument '{tok}' resolves outside allowed project roots "
                f"({', '.join(str(r) for r in allowed_roots)})."
            )
        if is_secret_file(resolved) or is_secret_file(cand):
            return (
                f"Refused: Argument '{tok}' is a credential/secret file "
                "(.env*, api_keys.json, *.pem, id_* keys, .git/*) and cannot be read."
            )
    return ""


def _validate_command(cmd_str: str, human_confirmed: bool = False) -> tuple[bool, list[str], str]:
    """
    Parses and checks command against strict allowlist.
    Returns (is_allowed, parsed_tokens, refusal_reason).

    human_confirmed is never taken from the model: _action_exec only passes True to
    ask "would this be allowed once a human confirms it on the HUD?".
    """
    confirm = human_confirmed
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

    if "/" in tokens[0] or tokens[0].startswith("~") or tokens[0] in (".", ".."):
        return False, [], (
            f"Refused: Path-qualified executable '{tokens[0]}' is not permitted. "
            "Use a bare command name; it is resolved from PATH."
        )

    binary = tokens[0].lower()

    # 1. Permanently blocked binaries
    if binary in BLOCKED_BINARIES:
        return False, [], (
            f"Refused: Command '{binary}' is blocked. 'run' is restricted to read-only inspection "
            "and safe build/test commands. Destructive and system-modifying operations are strictly disallowed."
        )

    # 2. Safe read binaries
    if binary in SAFE_READ_BINARIES:
        if binary in ("rg", "ripgrep"):
            for a in tokens[1:]:
                if _flag_base(a) in _RG_EXEC_FLAGS or (
                    re.fullmatch(r"-[A-Za-z]+", a) and "z" in a[1:]
                ):
                    return False, [], f"Refused: 'rg {a}' runs external programs and is not permitted."
        if binary in ("rg", "ripgrep") and any(
            _flag_base(a) in ("--follow", "-L") or (re.fullmatch(r"-[A-Za-z]+", a) and "L" in a[1:])
            for a in tokens[1:]
        ):
            return False, [], f"Refused: '{binary} --follow/-L' follows symlinks out of the project and is not permitted."
        if binary == "grep" and any(
            _flag_base(a) in ("-R", "--dereference-recursive") or (re.fullmatch(r"-[A-Za-z]+", a) and "R" in a[1:])
            for a in tokens[1:]
        ):
            return False, [], "Refused: 'grep -R' follows symlinks out of the project; use 'grep -r'."
        if binary in ("ls", "stat", "file") and any(
            _flag_base(a) in ("-L", "-H", "--dereference", "--dereference-command-line",
                              "--dereference-command-line-symlink-to-dir")
            or (re.fullmatch(r"-[A-Za-z]+", a) and ("L" in a[1:] or "H" in a[1:]))
            for a in tokens[1:]
        ):
            return False, [], f"Refused: '{binary}' symlink-following options are not permitted."
        if binary == "tree" and any(
            _flag_base(a) in ("-l", "--follow") or (re.fullmatch(r"-[A-Za-z]+", a) and "l" in a[1:])
            for a in tokens[1:]
        ):
            return False, [], "Refused: 'tree -l' follows symlinks out of the project and is not permitted."
        if binary in ("tail",) and any(_flag_base(a) in ("-f", "-F", "--follow", "--retry") for a in tokens[1:]):
            return False, [], "Refused: 'tail -f' never exits and is not permitted."
        if binary == "tree" and any(_flag_base(a) in ("-o", "-R") or a.startswith("-o") for a in tokens[1:]):
            return False, [], "Refused: 'tree -o' writes files and is not permitted."
        if binary == "file" and any(
            _flag_base(a) in ("--compile", "--magic-file", "--files-from")
            or (a.startswith("-") and not a.startswith("--") and any(ch in a[1:] for ch in "Cmf"))
            for a in tokens[1:]
        ):
            return False, [], "Refused: this 'file' option reads or writes arbitrary magic/list files."
        if binary == "wc" and any(_flag_base(a) == "--files0-from" for a in tokens[1:]):
            return False, [], "Refused: 'wc --files0-from' is not permitted."
        if binary in ("grep", "rg", "ripgrep") and any(
            _flag_base(a) in ("--file", "--ignore-file") or (a.startswith("-f") and not a.startswith("--"))
            for a in tokens[1:]
        ):
            return False, [], f"Refused: '{binary} -f/--file' reads patterns from arbitrary files and is not permitted."
        if binary == "diff" and any(_flag_base(a) in ("--diff-program",) for a in tokens[1:]):
            return False, [], "Refused: 'diff --diff-program' runs an external program and is not permitted."
        return True, tokens, ""

    # 3. 'find' safety check
    if binary == "find":
        if any(a in ("-L", "-H", "-follow") for a in tokens[1:]):
            return False, [], "Refused: 'find -L/-H/-follow' follows symlinks out of the project and is not permitted."
        for a in tokens[1:]:
            if a in _FIND_BLOCKED:
                return False, [], f"Refused: 'find' parameter '{a}' executes, deletes, or writes files and is strictly disallowed."
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
            if len(tokens) < 3 or tokens[2].lower() not in ("list", "show"):
                shown = tokens[2] if len(tokens) > 2 else "(push)"
                return False, [], (
                    f"Refused: 'git stash {shown}' is not permitted. Only 'git stash list' or 'git stash show' is allowed."
                )
        elif subcmd in ("branch", "tag"):
            ref_reason = _validate_git_ref_args(subcmd, tokens[2:])
            if ref_reason:
                return False, [], ref_reason
        elif subcmd == "remote":
            if len(tokens) > 2 and tokens[2].lower() not in ("-v", "--verbose", "get-url"):
                return False, [], f"Refused: 'git remote {tokens[2]}' is not permitted. Only 'git remote -v' is allowed."
        elif subcmd == "config":
            if any(_flag_base(a) in ("--list", "-l", "--get-regexp", "--get-urlmatch", "--show-origin", "--show-scope")
                   or a in ("list", "get") for a in tokens[2:]):
                return False, [], (
                    "Refused: 'git config --list/--get-regexp' can expose credential settings; "
                    "use 'git config --get <key>' for a specific key."
                )
            if not any(a in ("--get", "--get-all") for a in tokens[2:]):
                return False, [], "Refused: 'git config' is only permitted for reading one key (--get <key>)."
            keys = [a for a in tokens[2:] if not a.startswith("-")]
            if any(_GIT_SECRET_CONFIG_KEY.search(k) for k in keys):
                return False, [], "Refused: that git config key can hold credentials and is not readable."
            if any(_flag_base(a) in _GIT_CONFIG_WRITE_FLAGS for a in tokens[2:]):
                return False, [], "Refused: 'git config' write options are not permitted."

        for a in tokens[2:]:
            if _flag_base(a) == "--textconv":
                return False, [], "Refused: 'git --textconv' runs repository-configured filter programs."
            if _flag_base(a) in ("--output", "--output-directory", "-o", "--ext-diff", "--no-index", "--contents") or a.startswith("--output"):
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
        for a in tokens[2:]:
            if a == "--":
                break
            if _flag_base(a) in _CARGO_BLOCKED_FLAGS or a.startswith("-Z") or (a.startswith("+")):
                return False, [], (
                    f"Refused: 'cargo {a}' can override build configuration, toolchains, or output paths "
                    "and is not permitted."
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
        for a in tokens[1:]:
            if _flag_base(a) in _NODE_PM_BLOCKED_FLAGS:
                return False, [], f"Refused: '{binary} {a}' can change the shell, config, or directory and is not permitted."
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
                f"({', '.join(sorted(ALLOWED_BUILD_TARGETS))}). Custom targets need your confirmation on the HUD."
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
            for a in tokens[2:]:
                name = _flag_base(a).lstrip("-")
                if a.startswith("-") and name in _GO_BLOCKED_FLAGS:
                    return False, [], f"Refused: 'go {a}' can execute custom programs or write outside the repo."
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
        if not any(not t.startswith("-") for t in tokens[1:]):
            return False, [], (
                "Refused: bare 'make' runs the default recipe. Name an explicit target "
                f"({', '.join(sorted(ALLOWED_BUILD_TARGETS))})."
            )
        for t in tokens[1:]:
            if t.startswith("-"):
                if t in _MAKE_SAFE_FLAGS or _MAKE_J_RE.match(t):
                    continue
                return False, [], (
                    f"Refused: 'make {t}' is not in the safe option allowlist "
                    "(options like -f, --eval, -C, -I, -E can run arbitrary recipes)."
                )
            if "=" in t:
                return False, [], f"Refused: 'make {t}' variable overrides are not permitted."
            if t.lower() not in ALLOWED_BUILD_TARGETS:
                if confirm:
                    continue
                return False, [], (
                    f"Refused: 'make {t}' is not in the safe targets allowlist "
                    f"({', '.join(sorted(ALLOWED_BUILD_TARGETS))}). Custom targets need your confirmation on the HUD."
                )
        return True, tokens, ""

    # 12. 'just'
    if binary == "just":
        listing = any(t in ("--list", "-l", "--summary") for t in tokens[1:])
        if not listing and not any(not t.startswith("-") for t in tokens[1:]):
            return False, [], (
                "Refused: bare 'just' runs the default recipe. Name an explicit recipe "
                f"({', '.join(sorted(ALLOWED_BUILD_TARGETS))}) or use 'just --list'."
            )
        if listing and any(not t.startswith("-") for t in tokens[1:]):
            return False, [], "Refused: 'just --list' takes no recipe arguments."
        for t in tokens[1:]:
            if t.startswith("-"):
                if t in _JUST_SAFE_FLAGS:
                    continue
                return False, [], f"Refused: 'just {t}' is not in the safe option allowlist."
            if "=" in t:
                return False, [], f"Refused: 'just {t}' variable overrides are not permitted."
            if t.lower() not in ALLOWED_BUILD_TARGETS and t.lower() not in ("--list", "-l"):
                if confirm:
                    continue
                return False, [], (
                    f"Refused: 'just {t}' is not in the safe recipes allowlist "
                    f"({', '.join(sorted(ALLOWED_BUILD_TARGETS))}). Custom recipes need your confirmation on the HUD."
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


def _kill_process_group(proc: subprocess.Popen, sig: int) -> None:
    """Signal the whole process group started for a run (start_new_session=True)."""
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.send_signal(sig)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Action 1: Exec
# ---------------------------------------------------------------------------

def _action_exec(cmd_str: str, cwd_arg: str | None, timeout_arg: Any) -> str:
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
    is_allowed, tokens, reason = _validate_command(cmd_str)
    needs_confirmation = False
    if not is_allowed:
        # Custom npm/make/just targets are allowed only after a human confirms on the HUD.
        ok_if_confirmed, confirmed_tokens, _ = _validate_command(cmd_str, human_confirmed=True)
        if ok_if_confirmed:
            is_allowed, tokens, needs_confirmation = True, confirmed_tokens, True

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

    path_reason = _validate_path_args(tokens[0].lower(), tokens, cwd_path, allowed_roots)
    if path_reason:
        rec = RunRecord(run_id=run_id, cmd=cmd_str, cwd=str(cwd_path), status="refused", exit_code=-1, output=path_reason)
        with _RUN_LOCK:
            _RUN_HISTORY[run_id] = rec
        _audit_log(rec, reason=path_reason)
        return path_reason

    if Path(tokens[0]).name == "git" and _git_emits_patches(tokens):
        tracked = _tracked_secret_files(cwd_path)
        if tracked:
            reason = (
                "Refused: this repository tracks credential files "
                f"({', '.join(tracked[:5])}{', ...' if len(tracked) > 5 else ''}), so patch output "
                "(git diff/show/log -p) could print them. Use --stat/--name-only on git log, "
                "or untrack those files."
            )
            rec = RunRecord(run_id=run_id, cmd=cmd_str, cwd=str(cwd_path), status="refused", exit_code=-1, output=reason)
            with _RUN_LOCK:
                _RUN_HISTORY[run_id] = rec
            _audit_log(rec, reason=reason)
            return reason

    if needs_confirmation:
        return _request_human_confirmation(run_id, cmd_str, tokens, cwd_path, timeout_arg)

    return _spawn_run(run_id, cmd_str, tokens, cwd_path, timeout_arg)


_GIT_LOG_PATCH_FLAGS = frozenset({
    "-p", "-u", "--patch", "--patch-with-stat", "--patch-with-raw", "--cc", "-c", "-m",
    "--full-diff", "--diff-merges", "-L", "--remerge-diff", "--word-diff", "--color-words",
})


def _git_emits_patches(tokens: list[str]) -> bool:
    if len(tokens) < 2:
        return False
    sub = tokens[1].lower()
    args = tokens[2:]
    if sub in ("diff", "show"):
        return True
    if sub == "stash":
        return any(_flag_base(a) in ("-p", "--patch") for a in args)
    if sub == "log":
        return any(_flag_base(a) in _GIT_LOG_PATCH_FLAGS or a.startswith("-L") for a in args)
    return False


def _tracked_secret_files(cwd_path: Path) -> list[str]:
    """Secret files tracked anywhere in the repo containing cwd (they would appear in patch output)."""
    try:
        top = subprocess.run(
            ["git", "-C", str(cwd_path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5, env=git_safe_env(),
        )
        if top.returncode != 0:
            return []
        repo_root = top.stdout.strip() or str(cwd_path)
        res = subprocess.run(
            ["git", "-C", repo_root, "ls-files", "-z"],
            capture_output=True, text=True, timeout=5, env=git_safe_env(),
        )
    except Exception:
        return ["(could not list tracked files)"]
    if res.returncode != 0:
        return []
    return [f for f in res.stdout.split("\0") if f and is_secret_file(Path(f))]


def _request_human_confirmation(run_id: str, cmd_str: str, tokens: list[str], cwd_path: Path, timeout_arg: Any) -> str:
    """Park a custom build/test target behind core.confirm (the daemon's yes/no gate).

    The command runs only when a person answers yes (HUD button, or a bare yes/no
    reply that kurek_daemon resolves via confirm.resolve()). The model has no
    parameter that can approve it."""
    if _confirm.pending_title():
        reason = (
            f"Refused: another confirmation is already waiting ({_confirm.pending_title()}). "
            "Ask the user to answer it first."
        )
        rec = RunRecord(run_id=run_id, cmd=cmd_str, cwd=str(cwd_path), status="refused", exit_code=-1, output=reason)
        with _RUN_LOCK:
            _RUN_HISTORY[run_id] = rec
        _audit_log(rec, reason=reason)
        return reason

    rec = RunRecord(run_id=run_id, cmd=cmd_str, cwd=str(cwd_path), status="awaiting_confirmation")
    with _RUN_LOCK:
        _RUN_HISTORY[run_id] = rec
    _audit_log(rec, reason="awaiting human confirmation")

    frozen_tokens = list(tokens)

    def _run_after_confirmation() -> str:
        return _spawn_run(run_id, cmd_str, frozen_tokens, cwd_path, timeout_arg)

    msg = _confirm.request(
        key=f"run-{run_id}",
        title=f"Run '{cmd_str}'",
        detail=f"in {cwd_path}",
        run=_run_after_confirmation,
    )
    if not msg.startswith("[CONFIRMATION_PENDING]"):
        rec.status = "refused"
        rec.output = msg
        _audit_log(rec, reason="confirmation unavailable")
    return f"{msg} (run id {run_id}; read its output with action='read' once confirmed)"


def _spawn_run(run_id: str, cmd_str: str, tokens: list[str], cwd_path: Path, timeout_arg: Any) -> str:
    exe = shutil.which(tokens[0])
    if exe:
        tokens = [exe] + tokens[1:]

    # Configure timeout
    try:
        timeout = float(timeout_arg) if timeout_arg is not None else 30.0
    except (ValueError, TypeError):
        timeout = 30.0
    timeout = max(1.0, min(timeout, 300.0))

    # Clean non-interactive environment
    # Child processes never receive API keys, tokens, or remote-access settings.
    env = {k: v for k, v in os.environ.items() if not _is_sensitive_env(k)}
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env["CI"] = "1"
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    env["CARGO_TERM_COLOR"] = "never"
    env["npm_config_yes"] = "false"
    env["DEBIAN_FRONTEND"] = "noninteractive"
    # Neutralise repo-config hooks that make read-only git commands spawn programs.
    env = git_safe_env(_rg_env(env))
    if Path(tokens[0]).name in ("rg", "ripgrep"):
        tokens = tokens[:1] + ["--no-config"] + tokens[1:]
    if Path(tokens[0]).name == "git" and len(tokens) > 1:
        sub = tokens[1].lower()
        if sub in ("diff", "log", "show"):
            tokens = tokens[:2] + ["--no-ext-diff", "--no-textconv"] + tokens[2:]
        elif sub == "blame":
            tokens = tokens[:2] + ["--no-textconv"] + tokens[2:]

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
            stdin=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
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
            _kill_process_group(proc, signal.SIGKILL)
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            duration = time.time() - start_time
            record.exit_code = -1
            record.duration = duration
            record.status = "timed_out"

            combined = stdout or stderr or ""
            redacted, _ = _cap_output(_redact_secrets(combined.strip()), duration, -1)
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
            _kill_process_group(target_record.process, signal.SIGTERM)
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
        # Any model-supplied 'confirm' value is ignored on purpose: custom targets
        # go through the human confirmation gate in _request_human_confirmation.
        return _action_exec(cmd, cwd, timeout)
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
