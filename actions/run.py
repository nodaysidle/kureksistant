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
    Anything that runs repository code (tests, build scripts, Makefile/justfile recipes) waits for a
    human yes/no via core.confirm; the model cannot approve it.
  • Child environment is an allowlist (PATH, HOME, locale, toolchain dirs); output is streamed with a hard byte cap.
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
import select
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

from actions.codebase import (
    get_allowed_roots, _is_path_allowed, is_secret_file, git_safe_env, _rg_env,
    _SECRET_SEARCH_GLOBS, _dynamic_secret_targets, resolve_trusted_binary, trusted_binary,
    BINARY_NOT_FOUND,
)
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
_RUN_HISTORY_MAX = 200
_RUN_COUNTER = 0
_RUN_LOCK = threading.Lock()


def _cap_run_history() -> None:
    """Keep only the most recent _RUN_HISTORY_MAX records (insertion order)."""
    while len(_RUN_HISTORY) > _RUN_HISTORY_MAX:
        _RUN_HISTORY.pop(next(iter(_RUN_HISTORY)))


def _get_audit_log_path() -> Path:
    state_dir = Path.home() / ".local" / "state" / "kurek"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(state_dir, 0o700)
        except OSError:
            pass
        return state_dir / "run_audit.log"
    except Exception:
        fallback = Path.home() / ".config" / "kurek"
        fallback.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(fallback, 0o700)
        except OSError:
            pass
        return fallback / "run_audit.log"


def _audit_log(record: RunRecord, reason: str = "") -> None:
    path = _get_audit_log_path()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    exit_str = str(record.exit_code) if record.exit_code is not None else "none"
    extra = f" | REASON={reason}" if reason else ""
    line = (
        f"[{now_iso}] ID={record.run_id} | STATUS={record.status} | "
        f"EXIT={exit_str} | DUR={record.duration:.2f}s | "
        f"CWD={record.cwd} | CMD={shlex.quote(_redact_secrets(record.cmd))}{extra}\n"
    )
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Credential & Secret Redaction
# ---------------------------------------------------------------------------

# Key names whose values are credentials: anything ENDING in one of these words, any
# case, with any prefix (DB_PASSWORD, client_secret, GITHUB_TOKEN, x-api-key, ...).
# PASSWORD_MIN_LEN or TOKEN_URL do not end in one, so they are not secret keys.
_SECRET_KEY_NAME = (
    r"[A-Za-z0-9_\-]*(?:api[_\-]?key|apikey|secret|token|passw(?:or)?d|passphrase|auth|"
    r"auth[_\-]?key|private[_\-]?key|access[_\-]?key|credentials?)"
)
# key, optional quotes, ':' or '=' (not '==', ':='), then an unquoted value.
_UNQUOTED_SECRET_ASSIGN = re.compile(
    rf"""(?i)(?P<key>["']?(?P<name>{_SECRET_KEY_NAME})["']?)(?P<sep>[ \t]*[:=](?!=)[ \t]*)"""
    r"""(?!\[REDACTED)(?P<val>[^\s"'`,;&)}\]]+)"""
)
# Values that are not secrets: null/bool literals, type names and keywords, so code such as
# 'password=None', 'token: str', 'if password: return' stays readable.
_NOT_SECRET_VALUES = frozenset({
    "none", "null", "nil", "true", "false", "undefined", "~", "-",
    "str", "string", "bytes", "int", "bool", "float", "dict", "list", "any", "optional", "secretstr",
    "return", "pass", "raise", "not", "and", "or", "in", "is", "lambda", "await", "yield",
    "if", "else", "continue", "break", "impl", "mut", "const", "let", "var",
})
_CODE_RECEIVERS = frozenset({
    "self", "this", "cls", "os", "config", "cfg", "settings", "args", "opts", "options",
    "ctx", "env", "process", "request", "req", "props", "state",
})
_DOTTED_IDENT = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")


def _redact_unquoted_assignment(m: re.Match) -> str:
    """Redact the unquoted value of a secret-named key, whatever it looks like
    (PASSWORD=hunter-two has no digit and is still redacted), unless it is clearly code:
      - a null/bool literal, type name or keyword (password=None, token: str, if password: return);
      - a call, subscript or generic (load_key(), os.environ[...], Option<String>);
      - the key's own name (connect(password=password), {"token": token});
      - a dotted reference (token = self.token): with spaces around the separator, or
        starting with a well-known receiver (token=self.token, key=config.api_key).
    Anything else non-empty is replaced with [REDACTED_SECRET]."""
    name, sep, val = m.group("name"), m.group("sep"), m.group("val")
    low = val.lower()
    keep = (
        low in _NOT_SECRET_VALUES
        or any(c in val for c in "([<")
        or low == name.lower()
        or low == re.split(r"[_\-]", name.lower())[-1]
        or (_DOTTED_IDENT.fullmatch(val) is not None
            and ((sep.startswith((" ", "\t")) and sep.endswith((" ", "\t")))
                 or val.split(".", 1)[0].lower() in _CODE_RECEIVERS))
    )
    if keep:
        return m.group(0)
    return f"{m.group('key')}{sep}[REDACTED_SECRET]"


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
    # AWS access key IDs and secret access keys in assignments
    (re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|APKA|ABIA|ACCA)[A-Z0-9]{16}\b"), "[REDACTED_AWS_KEY_ID]"),
    (re.compile(
        r"""(?i)((?:aws_?secret_?access_?key|aws_?secret_?key|aws_?secret|secret_?access_?key)["']?\s*[:=]\s*["']?)"""
        r"""(?!\[REDACTED)([A-Za-z0-9/+=]{30,})"""
    ), r"\1[REDACTED_AWS_SECRET]"),
    (re.compile(r"""(?i)((?:aws_?session_?token)["']?\s*[:=]\s*["']?)(?!\[REDACTED)([A-Za-z0-9/+=]{20,})"""), r"\1[REDACTED_AWS_SECRET]"),
    # Common provider token prefixes
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"), "[REDACTED_GITLAB_TOKEN]"),
    (re.compile(r"\bgl(?:dt|rt|ptt|cbt|imt|soat|ffct)-[A-Za-z0-9_\-]{16,}"), "[REDACTED_GITLAB_TOKEN]"),
    (re.compile(r"\bxox[abprse]-[A-Za-z0-9\-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"\bxapp-[A-Za-z0-9\-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+"), "[REDACTED_SLACK_WEBHOOK]"),
    (re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"), "[REDACTED_STRIPE_KEY]"),
    (re.compile(r"\bwhsec_[A-Za-z0-9]{20,}"), "[REDACTED_STRIPE_KEY]"),
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}"), "[REDACTED_HF_TOKEN]"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}"), "[REDACTED_NPM_TOKEN]"),
    (re.compile(r"\bpypi-[A-Za-z0-9_\-]{50,}"), "[REDACTED_PYPI_TOKEN]"),
    (re.compile(r"\bshp(?:at|ca|pa|ss)_[a-fA-F0-9]{32}\b"), "[REDACTED_SHOPIFY_TOKEN]"),
    (re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"), "[REDACTED_SENDGRID_KEY]"),
    (re.compile(r"\bdop_v1_[a-f0-9]{64}\b"), "[REDACTED_DO_TOKEN]"),
    (re.compile(r"\b(?:AccountKey|SharedAccessKey)=[A-Za-z0-9+/=]{20,}"), "[REDACTED_AZURE_KEY]"),
    (re.compile(r"\bya29\.[A-Za-z0-9_\-]{20,}"), "[REDACTED_GOOGLE_OAUTH]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "[REDACTED_JWT]"),
    (re.compile(r"\bBearer\s+[a-zA-Z0-9_\-\.=]{20,}"), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"(https?://)([^:\s@/]+):([^@\s]+)@"), r"\1[REDACTED_USER]:[REDACTED_PASS]@"),
    # Container/package registry auth: docker/podman "auth", "identitytoken", "registrytoken",
    # "password" values (any length, quoted JSON/YAML), and .npmrc _auth/_authToken.
    (re.compile(
        r"""(?i)(["'](?:auth|identitytoken|registrytoken|password)["']\s*:\s*["'])(?!\[REDACTED)([^"']+)(?=["'])"""
    ), r"\1[REDACTED_REGISTRY_AUTH]"),
    (re.compile(
        r"""(?i)(\b(?:auth|identitytoken|registrytoken|password)\s*[:=]\s*["'])(?!\[REDACTED)([^"'\n]+)(?=["'])"""
    ), r"\1[REDACTED_REGISTRY_AUTH]"),
    (re.compile(r"""(?i)((?:^|[\s:])_auth(?:token)?\s*=\s*)(?!\[REDACTED)(\S+)"""), r"\1[REDACTED_REGISTRY_AUTH]"),
    # Quoted credential values (JSON/YAML/TOML/code literals): redact any non-empty value.
    (re.compile(
        rf"""(?i)(["']?{_SECRET_KEY_NAME}["']?\s*[:=]\s*["'])"""
        r"""(?!\[REDACTED)([^"'\s]+)(?=["'])"""
    ), r"\1[REDACTED_SECRET]"),
    # Unquoted values of secret-named keys (KEY=value, key: value): see _redact_unquoted_assignment.
    (_UNQUOTED_SECRET_ASSIGN, lambda m: _redact_unquoted_assignment(m)),
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
# Real cargo subcommands the tool accepts (built-ins, plus clippy). Anything else,
# e.g. a repo-defined alias like 'cargo status', is refused. All of them still
# need a human yes; only 'cargo --version/-V/--help' runs without one.
_CARGO_REAL_SUBCOMMANDS = frozenset({"check", "test", "build", "clippy", "bench", "tree", "metadata", "version"})

_GO_BLOCKED_FLAGS = frozenset({"exec", "toolexec", "vettool", "overlay", "modfile", "o", "pgo", "C"})

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

    def _needs_human(what: str) -> tuple[bool, list[str], str]:
        # Runs repository-controlled code (tests, conftest.py, build.rs, Makefile,
        # package.json scripts, Package.swift): only after a person says yes.
        if confirm:
            return True, tokens, ""
        return False, [], (
            f"Refused: '{what}' runs code from the repository, so it needs your confirmation on the HUD "
            "(or a yes/no reply)."
        )
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
        if binary == "diff" and any(
            _flag_base(a) in ("--recursive", "--new-file", "--unidirectional-new-file", "--exclude-from")
            or (re.fullmatch(r"-[A-Za-z]+", a) and "r" in a[1:])
            for a in tokens[1:]
        ):
            return False, [], (
                "Refused: recursive 'diff -r' can print secret files and is not permitted; "
                "diff two named files instead."
            )
        if binary == "grep" and any(
            _flag_base(a) in ("--include", "--exclude-from", "--files-from", "--null-data") for a in tokens[1:]
        ):
            return False, [], (
                "Refused: 'grep --include/--exclude-from' can override the secret-file excludes; "
                "pass a narrower path instead."
            )
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
        subcmd = tokens[1]
        if len(tokens) == 2 and subcmd in ("--version", "-V", "--help", "-h"):
            return True, tokens, ""  # handled by cargo itself, no subcommand/alias lookup
        if subcmd not in _CARGO_REAL_SUBCOMMANDS:
            return False, [], (
                f"Refused: 'cargo {subcmd}' is not in the allowlist of real cargo subcommands "
                f"({', '.join(sorted(_CARGO_REAL_SUBCOMMANDS))}); repository aliases are not run."
            )
        for a in tokens[2:]:
            if a == "--":
                break
            if _flag_base(a) in _CARGO_BLOCKED_FLAGS or a.startswith("-Z") or (a.startswith("+")):
                return False, [], (
                    f"Refused: 'cargo {a}' can override build configuration, toolchains, or output paths "
                    "and is not permitted."
                )
        # Every cargo subcommand loads repository config (.cargo/config.toml can alias,
        # wrap or run build scripts), so all of them need a human yes.
        return _needs_human(f"cargo {subcmd}")

    # 6. 'pytest'
    if binary == "pytest":
        return _needs_human("pytest")

    # 7. 'python' / 'python3'
    if binary in ("python", "python3"):
        if len(tokens) >= 3 and tokens[1] == "-m" and tokens[2].lower() in ("pytest", "unittest"):
            return _needs_human(f"{binary} -m {tokens[2].lower()}")
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
        if subcmd in ("--version", "-v", "--help", "-h"):
            return True, tokens, ""
        if subcmd == "test":
            return _needs_human(f"{binary} test")
        elif subcmd == "run":
            if len(tokens) < 3:
                return False, [], f"Refused: '{binary} run' requires a target (e.g. '{binary} run test')."
            target = tokens[2].lower()
            if target in ALLOWED_BUILD_TARGETS or confirm:
                return _needs_human(f"{binary} run {target}")
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
            if subcmd in ("test", "build", "vet"):
                # 'go build'/'go vet' run repository-controlled build steps and vet
                # plugins exactly like 'go test': a human must say yes first.
                return _needs_human(f"go {subcmd}")
            return True, tokens, ""
        return False, [], f"Refused: 'go {subcmd}' is not in the allowlist (allowed: test, build, vet, version)."

    # 10. 'swift'
    if binary == "swift":
        subcmd = tokens[1].lower() if len(tokens) > 1 else ""
        allowed_swift = {"build", "test", "--version", "--help"}
        if subcmd in allowed_swift:
            if subcmd in ("build", "test"):
                return _needs_human(f"swift {subcmd}")
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
        return _needs_human("make " + " ".join(t for t in tokens[1:] if not t.startswith("-")))

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
        if listing:
            return True, tokens, ""
        return _needs_human("just " + " ".join(t for t in tokens[1:] if not t.startswith("-")))

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


def is_unrestricted_shell() -> bool:
    val = (os.environ.get("KUREK_UNRESTRICTED_SHELL") or os.environ.get("KUREK_ALLOW_SHELL") or "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _spawn_unrestricted_shell(run_id: str, cmd_str: str, cwd_path: Path, timeout_arg: Any) -> str:
    try:
        timeout = float(timeout_arg) if timeout_arg is not None else 60.0
    except (ValueError, TypeError):
        timeout = 60.0
    timeout = max(1.0, min(timeout, 600.0))

    env = os.environ.copy()
    env["CI"] = "1"
    env["TERM"] = "dumb"

    record = RunRecord(
        run_id=run_id,
        cmd=cmd_str,
        cwd=str(cwd_path),
        status="running",
    )
    with _RUN_LOCK:
        _RUN_HISTORY[run_id] = record
        _cap_run_history()

    start_time = time.time()
    try:
        shell_bin = shutil.which("bash") or "/bin/sh"
        proc = subprocess.Popen(
            [shell_bin, "-c", cmd_str],
            cwd=str(cwd_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        record.process = proc

        stdout, stderr, outcome = _stream_output(proc, timeout)
        duration = time.time() - start_time
        combined = stdout
        if stderr:
            combined = f"{stdout}\n[STDERR]\n{stderr}" if stdout else stderr
        redacted = _redact_secrets(combined.strip())

        if outcome == "timeout":
            record.exit_code = -1
            record.duration = duration
            if record.status != "cancelled":
                record.status = "timed_out"
            capped_out, _ = _cap_output(redacted, duration, -1)
            record.output = f"TIMED OUT after {timeout}s.\n" + capped_out
            _audit_log(record, reason="timeout")
            return (
                f"TIMED OUT: Command '{cmd_str}' timed out after {timeout:.1f}s and was terminated.\n"
                f"Output before timeout:\n{record.output}"
            )

        if outcome == "capped":
            record.exit_code = -1
            record.duration = duration
            if record.status != "cancelled":
                record.status = "failed"
            capped_out, total_lines = _cap_output(redacted, duration, -1)
            record.output = (
                f"OUTPUT LIMIT: stopped after {_MAX_OUTPUT_BYTES} bytes and killed the process.\n" + capped_out
            )
            record.total_lines = total_lines
            _audit_log(record, reason="output cap")
            return (
                f"[{record.run_id}] OUTPUT LIMIT: '{cmd_str}' produced more than {_MAX_OUTPUT_BYTES} bytes "
                f"and was terminated.\n\n{record.output}"
            )

        exit_code = proc.returncode
        capped_output, total_lines = _cap_output(redacted, duration, exit_code)
        record.exit_code = exit_code
        record.duration = duration
        if record.status != "cancelled":
            record.status = "completed" if exit_code == 0 else "failed"
        record.output = capped_output
        record.total_lines = total_lines
        _audit_log(record)
        header = (
            f"[{record.run_id}] Exit: {exit_code} | Duration: {duration:.2f}s | "
            f"Cwd: {cwd_path.name}\nCommand: {cmd_str}\n\n"
        )
        return header + (capped_output or "(No output)")

    except Exception as e:
        duration = time.time() - start_time
        record.exit_code = -1
        record.duration = duration
        record.status = "failed"
        record.output = f"Execution error: {e}"
        _audit_log(record, reason=str(e))
        return f"Error executing command: {e}"


# ---------------------------------------------------------------------------
# Action 1: Exec
# ---------------------------------------------------------------------------

def _action_exec(cmd_str: str, cwd_arg: str | None, timeout_arg: Any) -> str:
    global _RUN_COUNTER

    if is_unrestricted_shell():
        cmd_clean = (cmd_str or "").strip()
        if not cmd_clean:
            return "Refused: Command string is empty."
        if cwd_arg:
            cwd_path = Path(os.path.expanduser(cwd_arg)).resolve()
            if not cwd_path.exists():
                cwd_path = Path.home()
        else:
            cwd_path = Path.home()

        with _RUN_LOCK:
            _RUN_COUNTER += 1
            run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{_RUN_COUNTER}"

        return _spawn_unrestricted_shell(run_id, cmd_clean, cwd_path, timeout_arg)

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
        # Test runners, build tools and custom targets run only after a human confirms
        # (HUD button or the daemon's yes/no reply); the model cannot approve them.
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
            _cap_run_history()
        _audit_log(rec, reason=reason)
        return reason

    path_reason = _validate_path_args(tokens[0].lower(), tokens, cwd_path, allowed_roots)
    if path_reason:
        rec = RunRecord(run_id=run_id, cmd=cmd_str, cwd=str(cwd_path), status="refused", exit_code=-1, output=path_reason)
        with _RUN_LOCK:
            _RUN_HISTORY[run_id] = rec
            _cap_run_history()
        _audit_log(rec, reason=path_reason)
        return path_reason

    scope_reason = _search_scope_reason(tokens, cwd_path)
    if scope_reason:
        rec = RunRecord(run_id=run_id, cmd=cmd_str, cwd=str(cwd_path), status="refused", exit_code=-1, output=scope_reason)
        with _RUN_LOCK:
            _RUN_HISTORY[run_id] = rec
            _cap_run_history()
        _audit_log(rec, reason=scope_reason)
        return scope_reason

    if Path(tokens[0]).name == "git" and _git_emits_patches(tokens):
        tracked = _tracked_secret_files(cwd_path) or _touched_secret_files(tokens, cwd_path)
        if tracked:
            reason = (
                "Refused: this command would show credential files "
                f"({', '.join(tracked[:5])}{', ...' if len(tracked) > 5 else ''}), so patch output "
                "(git diff/show/log -p, stash show -p) could print them, including deleted ones. "
                "Use --stat/--name-only on git log, or limit the range/paths."
            )
            rec = RunRecord(run_id=run_id, cmd=cmd_str, cwd=str(cwd_path), status="refused", exit_code=-1, output=reason)
            with _RUN_LOCK:
                _RUN_HISTORY[run_id] = rec
                _cap_run_history()
            _audit_log(rec, reason=reason)
            return reason

    # Refuse an untrusted binary (planted in a project, symlinked out of the trusted dirs)
    # up front instead of asking the human first. A binary that is simply not installed
    # is reported by _spawn_run, which resolves again (fail closed) after the yes.
    _exe, why = resolve_trusted_binary(tokens[0], allow_user_toolchains=needs_confirmation)
    if not _exe and BINARY_NOT_FOUND not in why:
        rec = RunRecord(run_id=run_id, cmd=cmd_str, cwd=str(cwd_path), status="refused", exit_code=-1, output=why)
        with _RUN_LOCK:
            _RUN_HISTORY[run_id] = rec
            _cap_run_history()
        _audit_log(rec, reason=why)
        return why

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
            [trusted_binary("git") or "/nonexistent/git", "-C", str(cwd_path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5, env=git_safe_env(),
        )
        if top.returncode != 0:
            return []
        repo_root = top.stdout.strip() or str(cwd_path)
        res = subprocess.run(
            [trusted_binary("git") or "/nonexistent/git", "-C", repo_root, "ls-files", "-z"],
            capture_output=True, text=True, timeout=5, env=git_safe_env(),
        )
    except Exception:
        return ["(could not list tracked files)"]
    if res.returncode != 0:
        return []
    return _secret_repo_paths(res.stdout.split("\0"), Path(repo_root))


def _secret_repo_paths(pieces: list[str], repo_root: Path) -> list[str]:
    """Repo-root-relative paths that are secrets: by name/dir (is_secret_file on the
    absolute path) or because they resolve to $REGISTRY_AUTH_FILE / $DOCKER_CONFIG targets."""
    dyn = _dynamic_secret_targets()
    hits: list[str] = []
    for piece in pieces:
        piece = piece.strip()
        if not piece or piece in hits:
            continue
        absolute = repo_root / piece
        try:
            resolved = absolute.resolve()
        except OSError:
            resolved = absolute
        if is_secret_file(Path(piece)) or is_secret_file(absolute) or resolved in dyn or absolute in dyn:
            hits.append(piece)
    return hits


def _git_repo_root(cwd_path: Path) -> str | None:
    try:
        top = subprocess.run(
            [trusted_binary("git") or "/nonexistent/git", "-C", str(cwd_path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5, env=git_safe_env(),
        )
    except Exception:
        return None
    if top.returncode != 0:
        return None
    return top.stdout.strip() or str(cwd_path)


_GIT_PATCH_STRIP = _GIT_LOG_PATCH_FLAGS | {"--word-diff-regex", "--stat", "--numstat", "--shortstat",
                                          "--name-status", "--raw", "--summary", "--dirstat"}


def _touched_secret_files(tokens: list[str], cwd_path: Path) -> list[str]:
    """Secret-named paths touched by the commits/range a patch command would print.

    Re-runs the same git command with --name-only (no renames, merges split per
    parent) so deleted and renamed secret files are seen. Fails closed."""
    sub = tokens[1].lower()
    args = tokens[2:]
    if sub == "stash":
        args = args[1:]
    if "--" in args:
        i = args.index("--")
        revs, paths = args[:i], args[i:]
    else:
        revs, paths = args, []
    kept: list[str] = []
    skip_next = False
    for a in revs:
        if skip_next:
            skip_next = False
            continue
        base = _flag_base(a)
        if a == "-L":
            skip_next = True
            continue
        if base in _GIT_PATCH_STRIP or a.startswith("-L") or a.startswith("--word-diff") \
                or a.startswith("--stat") or a.startswith("--dirstat"):
            continue
        if sub == "stash" and a in ("-u", "--include-untracked", "--only-untracked"):
            continue
        kept.append(a)
    # Forced flags go AFTER the user's flags so they win (e.g. over --relative / --pretty).
    names = ["--no-ext-diff", "--no-textconv", "--no-renames", "--no-relative", "--name-only", "-z"]
    if sub == "diff":
        cmd, forced = ["diff"], names
    elif sub == "stash" and tokens[2:3] == ["list"]:
        cmd, forced = ["log", "-g", "refs/stash"], [*names, "--format=", "--diff-merges=separate"]
    elif sub == "stash":
        cmd, forced = ["stash", "show"], [*names, "--include-untracked"]
    else:  # log, show
        cmd, forced = [sub], [*names, "--format=", "--diff-merges=separate"]
    repo_root = _git_repo_root(cwd_path)
    if repo_root is None:
        return []
    try:
        res = subprocess.run(
            [trusted_binary("git") or "/nonexistent/git", "-C", str(cwd_path), *cmd, *kept, *forced, *paths],
            capture_output=True, timeout=15, env=git_safe_env(),
        )
    except Exception:
        return ["(could not list the paths this command would show)"]
    if res.returncode != 0:
        return ["(could not list the paths this command would show)"]
    pieces = re.split(r"[\0\n\t]", res.stdout.decode("utf-8", "replace"))
    if sub == "diff":
        # A diff between commits can show a secret's content under a new name (it was
        # renamed or copied in between), so also list every path touched by the
        # commits between the endpoints.
        revs = [a for a in kept if not a.startswith("-")]
        if len(revs) == 1 and ".." in revs[0]:
            a_, _, b_ = revs[0].replace("...", "..").partition("..")
            spec = [f"{a_ or 'HEAD'}...{b_ or 'HEAD'}"]
        elif len(revs) == 1:
            spec = [f"{revs[0]}...HEAD"]
        elif len(revs) >= 2:
            spec = [f"{revs[0]}...{revs[1]}"]
        else:
            spec = []
        if spec:
            try:
                lres = subprocess.run(
                    [trusted_binary("git") or "/nonexistent/git", "-C", str(cwd_path), "log", "--no-ext-diff", "--no-textconv", "--no-renames",
                     "--no-relative", "--name-only", "-z", "--format=", "--diff-merges=separate", *spec, *paths],
                    capture_output=True, timeout=15, env=git_safe_env(),
                )
            except Exception:
                return ["(could not list the paths this command would show)"]
            if lres.returncode != 0:
                return ["(could not list the paths this command would show)"]
            pieces += re.split(r"[\0\n\t]", lres.stdout.decode("utf-8", "replace"))
    return _secret_repo_paths(pieces, Path(repo_root))


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
            _cap_run_history()
        _audit_log(rec, reason=reason)
        return reason

    rec = RunRecord(run_id=run_id, cmd=cmd_str, cwd=str(cwd_path), status="awaiting_confirmation")
    with _RUN_LOCK:
        _RUN_HISTORY[run_id] = rec
        _cap_run_history()
    _audit_log(rec, reason="awaiting human confirmation")

    frozen_tokens = list(tokens)

    def _run_after_confirmation() -> str:
        return _spawn_run(run_id, cmd_str, frozen_tokens, cwd_path, timeout_arg, human_confirmed=True)

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


# Only these variables are passed to children (plus LC_*). Everything else is dropped.
_CHILD_ENV_ALLOW = frozenset({
    "PATH", "HOME", "LANG", "LANGUAGE", "TERM", "USER", "LOGNAME", "TMPDIR",
    "CARGO_HOME", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN",
    "GOPATH", "GOCACHE", "GOMODCACHE", "GOROOT",
    "VIRTUAL_ENV", "PYTHONPATH",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
})


def _child_env_base() -> dict[str, str]:
    return {
        k: v for k, v in os.environ.items()
        if k in _CHILD_ENV_ALLOW or k.startswith("LC_")
    }


# Hard cap on combined stdout+stderr bytes read from a child.
_MAX_OUTPUT_BYTES = 1_000_000


def _stream_output(proc: subprocess.Popen, timeout: float, cap: int | None = None) -> tuple[str, str, str]:
    """Read stdout/stderr incrementally (select + os.read) with a hard byte cap.

    Returns (stdout, stderr, outcome) with outcome "exited", "timeout" or "capped".
    On timeout or cap the whole process group is SIGKILLed and reading stops; no
    more than `cap` bytes are ever held in memory."""
    cap = _MAX_OUTPUT_BYTES if cap is None else cap
    bufs: dict[str, list[bytes]] = {"out": [], "err": []}
    fds: dict[int, str] = {}
    for name, stream in (("out", proc.stdout), ("err", proc.stderr)):
        if stream is not None:
            fds[stream.fileno()] = name
    total = 0
    deadline = time.monotonic() + timeout
    outcome = "exited"
    while fds:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            outcome = "timeout"
            break
        ready, _, _ = select.select(list(fds), [], [], min(remaining, 0.1))
        for fd in ready:
            chunk = os.read(fd, min(65536, cap - total + 1))
            if not chunk:
                fds.pop(fd, None)
                continue
            if total + len(chunk) > cap:
                chunk = chunk[: cap - total]
                outcome = "capped"
            bufs[fds[fd]].append(chunk)
            total += len(chunk)
            if outcome == "capped":
                break
        if outcome == "capped":
            break
    if outcome == "exited":
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            outcome = "timeout"
    if outcome != "exited":
        _kill_process_group(proc, signal.SIGKILL)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    for stream in (proc.stdout, proc.stderr):
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass
    out = b"".join(bufs["out"]).decode("utf-8", "replace")
    err = b"".join(bufs["err"]).decode("utf-8", "replace")
    return out, err, outcome


_SECRET_SCOPE_DIRS = frozenset({".git", ".docker"})


def _search_scope_reason(tokens: list[str], cwd_path: Path) -> str:
    """rg/grep must not search from inside .git/ or .docker/ (or $DOCKER_CONFIG):
    relative paths there ('./config', './config.json') escape the exclude globs."""
    if tokens[0].lower() not in ("rg", "ripgrep", "grep"):
        return ""
    scopes = [cwd_path]
    for _tok, value in _path_candidates(tokens):
        try:
            cand = Path(os.path.expanduser(value))
            if not cand.is_absolute():
                cand = cwd_path / cand
            if cand.is_dir():
                scopes.append(cand)
        except Exception:
            continue
    docker_dirs: set[Path] = set()
    if os.environ.get("DOCKER_CONFIG"):
        try:
            docker_dirs.add(Path(os.path.expanduser(os.environ["DOCKER_CONFIG"])).resolve())
        except OSError:
            pass
    for s in scopes:
        try:
            r = s.resolve()
        except OSError:
            continue
        if _SECRET_SCOPE_DIRS.intersection(r.parts) or r in docker_dirs \
                or any(d in r.parents for d in docker_dirs):
            return (
                f"Refused: searching inside '{s}' could print credential files "
                "(.git/, .docker/, $DOCKER_CONFIG); search the project root instead."
            )
    return ""


# grep --exclude matches base names only, so path-shaped secrets are covered by
# their directory (--exclude-dir) or by the base name.
_GREP_EXCLUDE_NAMES = (
    ".env*", "api_keys.json", ".git-credentials", ".netrc", ".pgpass", "credentials.json",
    "*.pem", "*.key", "*.p12", "*.pfx", "id_*", ".dockercfg", ".npmrc", ".pypirc", ".yarnrc.yml",
    "auth.json", "credentials", "credentials.toml",
)
_GREP_EXCLUDE_DIRS = (".git", ".docker")


def _with_secret_excludes(tokens: list[str]) -> list[str]:
    """Append secret-file excludes AFTER the user's flags (before any '--').

    rg: later globs win over earlier ones and --no-ignore/-u/--hidden do not
    disable explicit -g overrides. grep: the last matching --include/--exclude
    wins and --include is refused, so the excludes always apply."""
    name = Path(tokens[0]).name
    dyn_names = sorted({p.name for p in _dynamic_secret_targets()})
    extra: list[str] = []
    if name in ("rg", "ripgrep"):
        for g in (*_SECRET_SEARCH_GLOBS, "**/.git/**", "auth.json", "credentials", "credentials.toml",
                  *dyn_names):
            extra += ["-g", f"!{g}", "--iglob", f"!{g}"]
    else:
        extra += [f"--exclude={g}" for g in (*_GREP_EXCLUDE_NAMES, *dyn_names)]
        extra += [f"--exclude-dir={d}" for d in _GREP_EXCLUDE_DIRS]
    if "--" in tokens[1:]:
        i = tokens.index("--", 1)
        return tokens[:i] + extra + tokens[i:]
    return tokens + extra


def _spawn_run(run_id: str, cmd_str: str, tokens: list[str], cwd_path: Path, timeout_arg: Any,
               human_confirmed: bool = False) -> str:
    # Only trusted dirs (see actions.codebase.SYSTEM_BIN_DIRS): never the first match on
    # PATH. User toolchain dirs only for commands a human has just said yes to.
    exe, why = resolve_trusted_binary(tokens[0], allow_user_toolchains=human_confirmed)
    if not exe:
        rec = RunRecord(run_id=run_id, cmd=cmd_str, cwd=str(cwd_path), status="refused", exit_code=-1, output=why)
        with _RUN_LOCK:
            _RUN_HISTORY[run_id] = rec
            _cap_run_history()
        _audit_log(rec, reason=why)
        return why
    tokens = [exe] + tokens[1:]

    # Configure timeout
    try:
        timeout = float(timeout_arg) if timeout_arg is not None else 30.0
    except (ValueError, TypeError):
        timeout = 30.0
    timeout = max(1.0, min(timeout, 300.0))

    # Clean non-interactive environment built from an ALLOWLIST: children never see
    # API keys, cloud credentials, tokens or remote-access settings.
    env = _child_env_base()
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
    if Path(tokens[0]).name in ("rg", "ripgrep", "grep"):
        tokens = _with_secret_excludes(tokens)
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
        _cap_run_history()

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
            start_new_session=True,
        )
        record.process = proc

        stdout, stderr, outcome = _stream_output(proc, timeout)
        duration = time.time() - start_time
        combined = stdout
        if stderr:
            combined = f"{stdout}\n[STDERR]\n{stderr}" if stdout else stderr
        redacted = _redact_secrets(combined.strip())

        if outcome == "timeout":
            record.exit_code = -1
            record.duration = duration
            if record.status != "cancelled":
                record.status = "timed_out"
            capped_out, _ = _cap_output(redacted, duration, -1)
            record.output = f"TIMED OUT after {timeout}s.\n" + capped_out
            _audit_log(record, reason="timeout")
            return (
                f"TIMED OUT: Command '{cmd_str}' timed out after {timeout:.1f}s and was terminated.\n"
                f"Output before timeout:\n{record.output}"
            )

        if outcome == "capped":
            record.exit_code = -1
            record.duration = duration
            if record.status != "cancelled":
                record.status = "failed"
            capped_out, total_lines = _cap_output(redacted, duration, -1)
            record.output = (
                f"OUTPUT LIMIT: stopped after {_MAX_OUTPUT_BYTES} bytes and killed the process.\n" + capped_out
            )
            record.total_lines = total_lines
            _audit_log(record, reason="output cap")
            return (
                f"[{record.run_id}] OUTPUT LIMIT: '{cmd_str}' produced more than {_MAX_OUTPUT_BYTES} bytes "
                f"and was terminated.\n\n{record.output}"
            )

        exit_code = proc.returncode
        capped_output, total_lines = _cap_output(redacted, duration, exit_code)
        record.exit_code = exit_code
        record.duration = duration
        if record.status != "cancelled":
            record.status = "completed" if exit_code == 0 else "failed"
        record.output = capped_output
        record.total_lines = total_lines
        _audit_log(record)
        header = (
            f"[{record.run_id}] Exit: {exit_code} | Duration: {duration:.2f}s | "
            f"Cwd: {cwd_path.name}\nCommand: {cmd_str}\n\n"
        )
        return header + (capped_output or "(No output)")

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

    if action in ("exec", "shell"):
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
            "Supported actions: 'exec', 'shell', 'read', 'list', 'cancel'."
        )


TOOL = {
    "name": "run",
    "description": (
        "Workstation command and shell runner. "
        "Executes commands and shell scripts across the system (e.g. bash, systemctl, pacman, git, cargo, build/test scripts), "
        "captures stdout/stderr, exit code, and duration."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "The run action: 'exec' or 'shell' (execute shell command or script), "
                    "'read' (inspect output of recent run), 'list' (list recent runs), "
                    "or 'cancel' (terminate running process)."
                ),
                "enum": ["exec", "shell", "read", "list", "cancel"],
            },
            "cmd": {
                "type": "STRING",
                "description": "Command string or shell script to execute (e.g. 'bash -c', system commands, dev tools).",
            },
            "cwd": {
                "type": "STRING",
                "description": "Working directory path.",
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
