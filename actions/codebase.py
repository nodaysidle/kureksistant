"""
actions/codebase.py — Repo-aware search, indexing, and read access for Kurek.
Auto-discovered by core/action_loader.py.

Capabilities:
  • 'search': Keyword / regex search across files with context lines (via ripgrep).
  • 'tree': Filtered directory listing respecting ignore rules and depth.
  • 'read': Safe file reader supporting line ranges (never dumps 4000-line files).
  • 'find': Fast file locator by glob pattern or extension.
  • 'outline': Symbol-level overview (functions, structs, traits, classes, interfaces).
  • 'index': Builds or refreshes per-repo snapshot index and reports staleness.
  • 'status': Per-repo health summary: language, branch, dirty state, last commit, index freshness.

Scope Discipline:
  • Strictly read-only. No writes, deletes, or modifications ever.
  • Root-scoped: operations outside allowed project roots are strictly refused.
  • Large results are truncated with explicit markers and hints.
  • Binary files are safely detected and skipped.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Default allowed project roots if ~/.config/kurek/codebase_roots is missing
DEFAULT_ROOTS = [
    Path.home() / "Projects",
    Path.home() / "dev",
]

# Standard directories and files to always ignore across tree, search, find, and index
STANDARD_IGNORES = frozenset({
    ".git",
    "node_modules",
    "target",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".next",
    ".turbo",
    ".idea",
    ".vscode",
    ".tox",
    "vendor",
})

# Extensions commonly considered binary
BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z",
    ".exe", ".bin", ".dylib", ".so", ".a", ".o", ".pyc", ".whl",
    ".woff", ".woff2", ".ttf", ".eot",
    ".db", ".sqlite", ".sqlite3",
})


def _get_config_dir() -> Path:
    return Path.home() / ".config" / "kurek"


def _get_cache_dir() -> Path:
    cache = Path.home() / ".cache" / "kurek" / "codebase_index"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def get_allowed_roots() -> list[Path]:
    """Retrieve allowed project roots from env, config file, or defaults."""
    roots: list[Path] = []

    # 1. Environment variable override (comma or colon separated)
    env_roots = os.environ.get("KUREK_CODEBASE_ROOTS") or os.environ.get("KUREK_PROJECT_ROOTS")
    if env_roots:
        for r in re.split(r"[:;,]", env_roots):
            r_str = r.strip()
            if r_str:
                p = Path(os.path.expanduser(r_str)).resolve()
                if p not in roots:
                    roots.append(p)
        if roots:
            return roots

    # 2. Config file ~/.config/kurek/codebase_roots
    cfg_file = _get_config_dir() / "codebase_roots"
    if cfg_file.exists():
        try:
            for line in cfg_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    p = Path(os.path.expanduser(line)).resolve()
                    if p not in roots:
                        roots.append(p)
        except Exception:
            pass

    # 3. Default roots if config is empty or missing
    if not roots:
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        if not cfg_file.exists():
            try:
                cfg_file.write_text(
                    "# Kurek Codebase Allowed Project Roots\n"
                    "~/Projects\n"
                    "~/dev\n",
                    encoding="utf-8",
                )
            except Exception:
                pass
        for def_r in DEFAULT_ROOTS:
            resolved = def_r.resolve()
            if resolved not in roots:
                roots.append(resolved)

    return roots


_SECRET_FILE_NAMES = frozenset({
    "api_keys.json", ".git-credentials", ".netrc", ".pgpass", "credentials.json", ".envrc",
    # container / package registry auth
    ".dockercfg", ".npmrc", ".pypirc", ".yarnrc.yml",
})
# (parent dir, file name) pairs that hold registry credentials:
#   ~/.docker/config.json, ~/.config/containers/auth.json, $XDG_RUNTIME_DIR/containers/auth.json,
#   /etc/containers/auth.json, ~/.cargo/credentials(.toml), ~/.gem/credentials
_SECRET_DIR_FILES = frozenset({
    (".docker", "config.json"), ("containers", "auth.json"),
    (".cargo", "credentials"), (".cargo", "credentials.toml"), (".gem", "credentials"),
})
# Globs excluded from rg searches; a superset of is_secret_file().
_SECRET_SEARCH_GLOBS = (
    ".env", ".env*", "api_keys.json", ".git-credentials", ".netrc", ".pgpass", "credentials.json",
    "*.pem", "*.key", "*.p12", "*.pfx", "id_*", ".git",
    ".dockercfg", ".npmrc", ".pypirc", ".yarnrc.yml",
    "**/.docker/config.json", "**/containers/auth.json",
    "**/.cargo/credentials", "**/.cargo/credentials.toml", "**/.gem/credentials",
)
_ENV_TEMPLATE_NAMES = frozenset({".env.example", ".env.sample", ".env.template"})
_SSH_KEY_RE = re.compile(r"^id_[a-z0-9_\-]+$")


def is_secret_file(path: Path) -> bool:
    """True for files that hold credentials: .env*, api_keys.json, *.pem, id_* keys, .git/config."""
    name = path.name
    lower = name.lower()
    if lower in _SECRET_FILE_NAMES:
        return True
    parent = path.parent.name.lower()
    if (parent, lower) in _SECRET_DIR_FILES:
        return True
    if lower in ("auth.json", "config.json") or os.environ.get("REGISTRY_AUTH_FILE"):
        if _is_registry_auth_location(path):
            return True
    if lower.startswith(".env") and lower not in _ENV_TEMPLATE_NAMES:
        return True
    if lower.endswith((".pem", ".key", ".p12", ".pfx")):
        return True
    if _SSH_KEY_RE.match(lower):
        return True
    if ".git" in path.parts:
        return True
    return False


def _dynamic_secret_targets() -> set[Path]:
    """Resolved $REGISTRY_AUTH_FILE and $DOCKER_CONFIG/config.json (they can have any name)."""
    out: set[Path] = set()
    for var, fname in (("REGISTRY_AUTH_FILE", None), ("DOCKER_CONFIG", "config.json")):
        val = os.environ.get(var)
        if not val:
            continue
        target = Path(os.path.expanduser(val))
        if fname:
            target = target / fname
        try:
            out.add(target.resolve())
        except OSError:
            continue
    return out


def _is_dynamic_secret(path: Path, targets: set[Path] | None = None) -> bool:
    targets = _dynamic_secret_targets() if targets is None else targets
    if not targets:
        return False
    try:
        return path.resolve() in targets
    except OSError:
        return True


def _is_registry_auth_location(path: Path) -> bool:
    """REGISTRY_AUTH_FILE / DOCKER_CONFIG may point anywhere; treat those targets as secrets."""
    for var, fname in (("REGISTRY_AUTH_FILE", None), ("DOCKER_CONFIG", "config.json")):
        val = os.environ.get(var)
        if not val:
            continue
        target = Path(os.path.expanduser(val))
        if fname:
            target = target / fname
        try:
            if path.resolve() == target.resolve():
                return True
        except OSError:
            continue
    return False


_GIT_SAFE_OVERRIDES = (
    ("core.fsmonitor", "false"),
    ("core.pager", "cat"),
    ("core.hooksPath", os.devnull),
    ("diff.external", ""),
    ("credential.helper", ""),
)
_GIT_SCRUB_ENV = (
    "GIT_EXTERNAL_DIFF", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS", "GIT_CONFIG_PARAMETERS",
    # repository redirection: git must operate on the repo at cwd, nothing else
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
    "GIT_CONFIG", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM",
)


def git_safe_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for git children: repo config cannot make read-only git spawn programs."""
    env = dict(os.environ if base is None else base)
    for key in _GIT_SCRUB_ENV:
        env.pop(key, None)
    env["GIT_PAGER"] = "cat"
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Pathspecs are literal: no globs ('.env*') and no magic (':(top)', ':(glob)').
    env["GIT_LITERAL_PATHSPECS"] = "1"
    env["GIT_CONFIG_COUNT"] = str(len(_GIT_SAFE_OVERRIDES))
    for i, (k, v) in enumerate(_GIT_SAFE_OVERRIDES):
        env[f"GIT_CONFIG_KEY_{i}"] = k
        env[f"GIT_CONFIG_VALUE_{i}"] = v
    return env


def _rg_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """ripgrep must not load a config file (it could enable --pre or --follow)."""
    env = dict(os.environ if base is None else base)
    env.pop("RIPGREP_CONFIG_PATH", None)
    return env


def _is_regular_file(path: Path) -> bool:
    """True only for regular files (after following symlinks): no FIFO/device/socket."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except OSError:
        return False


def _redact(text: str) -> str:
    from actions.run import _redact_secrets  # lazy: actions.run imports this module
    return _redact_secrets(text)


def _is_path_allowed(target: Path, allowed_roots: list[Path]) -> bool:
    """True if target path resolves within one of the allowed roots."""
    try:
        res = target.resolve()
        for root in allowed_roots:
            try:
                res.relative_to(root.resolve())
                return True
            except ValueError:
                continue
    except Exception:
        pass
    return False


def _resolve_target(
    root_arg: str | None,
    path_arg: str | None,
    allowed_roots: list[Path],
) -> tuple[Path | None, Path | None, str | None]:
    """
    Resolves repository root and specific path within allowed roots.
    Returns (repo_root, target_path, error_string).
    """
    resolved_root: Path | None = None
    resolved_target: Path | None = None

    # Resolve repo root first if specified
    if root_arg:
        r_str = root_arg.strip()
        candidate = Path(os.path.expanduser(r_str)).resolve()
        if candidate.exists() and _is_path_allowed(candidate, allowed_roots):
            resolved_root = candidate
        else:
            # Check by name inside allowed roots (e.g. root="kurekizmo" or "nodaysidle-flowstate")
            matched: list[Path] = []
            for ar in allowed_roots:
                if ar.is_dir():
                    direct = (ar / r_str).resolve()
                    if direct.is_dir() and _is_path_allowed(direct, allowed_roots):
                        matched.append(direct)
                    else:
                        # Scan one level down (e.g. ~/dev/nodaysidle/kurekizmo)
                        try:
                            for sub in ar.iterdir():
                                if sub.is_dir():
                                    sub_target = (sub / r_str).resolve()
                                    if sub_target.is_dir() and _is_path_allowed(sub_target, allowed_roots):
                                        matched.append(sub_target)
                        except Exception:
                            pass
            if matched:
                resolved_root = matched[0]
            else:
                return None, None, (
                    f"Refused: Project root '{root_arg}' could not be found within allowed roots "
                    f"({', '.join(str(r) for r in allowed_roots)})."
                )

    # Resolve path_arg
    if path_arg:
        p_str = path_arg.strip()
        candidate_path = Path(os.path.expanduser(p_str))
        if candidate_path.is_absolute():
            resolved_target = candidate_path.resolve()
            if not _is_path_allowed(resolved_target, allowed_roots):
                return None, None, (
                    f"Refused: Path '{path_arg}' resolves outside allowed project roots."
                )
            if resolved_root is None:
                # Walk up to find repo root (.git) or fallback to allowed root
                curr = resolved_target if resolved_target.is_dir() else resolved_target.parent
                while curr != curr.parent:
                    if (curr / ".git").exists():
                        resolved_root = curr
                        break
                    curr = curr.parent
                if resolved_root is None:
                    resolved_root = resolved_target if resolved_target.is_dir() else resolved_target.parent
        else:
            # Relative path
            base = resolved_root
            if base is None:
                cwd = Path.cwd().resolve()
                if _is_path_allowed(cwd, allowed_roots):
                    base = cwd
                else:
                    base = allowed_roots[0]
            resolved_target = (base / p_str).resolve()
            if not _is_path_allowed(resolved_target, allowed_roots):
                return None, None, (
                    f"Refused: Path '{path_arg}' relative to '{base}' resolves outside allowed roots."
                )
            if resolved_root is None:
                resolved_root = base
    else:
        # No path_arg given
        if resolved_root is not None:
            resolved_target = resolved_root
        else:
            cwd = Path.cwd().resolve()
            if _is_path_allowed(cwd, allowed_roots):
                resolved_root = cwd
                resolved_target = cwd
            elif allowed_roots:
                resolved_root = allowed_roots[0]
                resolved_target = allowed_roots[0]
            else:
                return None, None, "Refused: No allowed project roots available."

    return resolved_root, resolved_target, None


# Hard caps: codebase never reads more than this from one file, and never keeps more
# than this much search output in memory.
_READ_MAX_BYTES = 1_000_000
_SEARCH_MAX_BYTES = 1_000_000


def _read_capped(file_path: Path, cap: int | None = None) -> tuple[str, bool]:
    """Read at most `cap` bytes; returns (text, truncated). Never reads the whole file."""
    cap = _READ_MAX_BYTES if cap is None else cap
    with open(file_path, "rb") as f:
        data = f.read(cap + 1)
    truncated = len(data) > cap
    return data[:cap].decode("utf-8", "replace"), truncated


def _run_rg_capped(cmd: list[str], timeout: float, cap: int | None = None) -> tuple[str, str, str, int | None]:
    """Run rg in its own process group and stream output under _SEARCH_MAX_BYTES.

    Returns (stdout, stderr, outcome, returncode); outcome is exited/timeout/capped.
    On cap or timeout the whole group is killed (see actions.run._stream_output)."""
    from actions.run import _stream_output  # lazy: actions.run imports this module
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
        env=_rg_env(), start_new_session=True,
    )
    out, err, outcome = _stream_output(proc, timeout, cap=_SEARCH_MAX_BYTES if cap is None else cap)
    return out, err, outcome, proc.returncode


def _is_binary(file_path: Path) -> bool:
    """Quickly check if file is binary by extension or null byte in first 1024 bytes."""
    if file_path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    if not _is_regular_file(file_path):
        return True  # never open FIFOs, devices or sockets
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(1024)
            if b"\x00" in chunk:
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Action 1: Search
# ---------------------------------------------------------------------------

def _action_search(target_dir: Path, query: str, depth: int | None = None) -> str:
    if not query or not query.strip():
        return "Error: Search query cannot be empty."

    query_str = query.strip()
    rg_path = shutil.which("rg")
    search_capped = False

    if rg_path:
        cmd = [
            rg_path,
            "--no-config",
            "-n",
            "-C", "2",
            "--max-columns", "500",
            "--color", "never",
            "--hidden",
            "-g", "!.git",
            "-g", "!node_modules",
            "-g", "!target",
            "-g", "!dist",
            "-g", "!build",
            "-g", "!.venv",
            "-g", "!__pycache__",
            "-g", "!*.lock",
            "--null",
        ]
        for secret_glob in _SECRET_SEARCH_GLOBS:
            cmd.extend(["--iglob", f"!{secret_glob}"])
        if depth is not None and depth > 0:
            cmd.extend(["--max-depth", str(depth)])
        cmd.extend(["-e", query_str, str(target_dir)])

        try:
            out, err, outcome, rc = _run_rg_capped(cmd, timeout=12)
            if outcome == "timeout":
                return f"Search timed out after 12s. Narrow query or specify a subpath."
            raw_out = out.strip()
            if outcome == "capped":
                search_capped = True
                raw_out = raw_out.rsplit("\n", 1)[0] if "\n" in raw_out else raw_out
            if not raw_out and err and rc not in (0, 1):
                return f"Search notice: {_redact(err.strip()[:2000])}"
            if not search_capped:
                # .env templates are excluded by the '.env*' secret glob above; search exactly
                # those names in a second, whitelisted pass under the remaining byte budget.
                tcmd = [rg_path, "--no-config", "-n", "-C", "2", "--max-columns", "500", "--color", "never",
                        "--hidden", "--null"]
                for name in sorted(_ENV_TEMPLATE_NAMES):
                    tcmd.extend(["-g", name])
                if depth is not None and depth > 0:
                    tcmd.extend(["--max-depth", str(depth)])
                tcmd.extend(["-e", query_str, str(target_dir)])
                budget = max(0, _SEARCH_MAX_BYTES - len(out.encode("utf-8", "replace")))
                if budget:
                    t_out, _t_err, t_outcome, _t_rc = _run_rg_capped(tcmd, timeout=12, cap=budget)
                    t_raw = t_out.strip()
                    if t_outcome == "capped":
                        search_capped = True
                        t_raw = t_raw.rsplit("\n", 1)[0] if "\n" in t_raw else ""
                    if t_outcome != "timeout" and t_raw:
                        raw_out = (raw_out + "\n--\n" + t_raw) if raw_out else t_raw
            dyn = _dynamic_secret_targets()
            lines = []
            for raw in raw_out.splitlines():
                if "\0" not in raw:
                    lines.append(raw)  # '--' context separator
                    continue
                fpath, rest = raw.split("\0", 1)
                fp = Path(fpath)
                try:
                    real_fp = fp.resolve()
                except OSError:
                    continue
                if is_secret_file(fp) or is_secret_file(real_fp) or _is_dynamic_secret(fp, dyn):
                    continue  # name, symlink target and resolved REGISTRY_AUTH_FILE / DOCKER_CONFIG
                lines.append(f"{fpath}:{rest}")
            # drop context separators left dangling by filtered files
            cleaned: list[str] = []
            for ln in lines:
                if ln == "--" and (not cleaned or cleaned[-1] == "--"):
                    continue
                cleaned.append(ln)
            while cleaned and cleaned[-1] == "--":
                cleaned.pop()
            lines = cleaned
            if not lines:
                return f"No matches found for '{query_str}' in {target_dir.name}."
        except Exception as e:
            lines = []
    else:
        # Fallback pure Python search
        lines = []
        collected = 0
        try:
            pattern = re.compile(re.escape(query_str), re.IGNORECASE)
            for root, dirs, files in os.walk(target_dir):
                if search_capped:
                    break
                dirs[:] = [d for d in dirs if d not in STANDARD_IGNORES and not d.startswith(".")]
                for fname in sorted(files):
                    fpath = Path(root) / fname
                    real = fpath.resolve()
                    if fpath.is_symlink() and not _is_path_allowed(real, get_allowed_roots()):
                        continue  # symlink leaves the project roots
                    if _is_binary(fpath) or is_secret_file(fpath) or is_secret_file(real) \
                            or _is_dynamic_secret(fpath):
                        continue
                    try:
                        text, _ = _read_capped(fpath)
                        for idx, l in enumerate(text.splitlines()):
                            if pattern.search(l):
                                rel = fpath.relative_to(target_dir)
                                entry = f"{rel}:{idx+1}:{l[:500]}"
                                collected += len(entry.encode("utf-8", "replace")) + 1
                                if collected > _SEARCH_MAX_BYTES:
                                    search_capped = True
                                    break
                                lines.append(entry)
                    except Exception:
                        pass
                    if search_capped:
                        break
        except Exception as e:
            return f"Search error: {e}"

    if not lines:
        return f"No matches found for '{query_str}' in {target_dir.name}."

    total_lines = len(lines)
    max_lines = 100
    displayed = lines[:max_lines]

    header = f"Search '{query_str}' in {target_dir.name} ({total_lines} total lines/matches):\n"
    body = "\n".join(displayed)

    if total_lines > max_lines:
        footer = (
            f"\n\n[Truncated: showing first {max_lines} of {total_lines} lines. "
            f"Narrow your query with path='<subpath>' or a more specific term.]"
        )
    else:
        footer = ""
    if search_capped:
        footer += (
            f"\n\n[Output limit: search stopped after {_SEARCH_MAX_BYTES} bytes. "
            "Narrow your query or path.]"
        )

    return _redact(header + body + footer)


# ---------------------------------------------------------------------------
# Action 2: Tree
# ---------------------------------------------------------------------------

# Limits for tree: entries read per directory and overall, plus wall time.
_TREE_MAX_DIR_ENTRIES = 2_000
_TREE_MAX_TOTAL_ENTRIES = 20_000
_TREE_MAX_SECONDS = 5.0


def _scan_dir_capped(current: Path, cap: int) -> tuple[list[os.DirEntry], bool]:
    """os.scandir with early stop: never holds more than `cap` entries of one directory."""
    entries: list[os.DirEntry] = []
    more = False
    with os.scandir(current) as it:
        for e in it:
            if e.name in STANDARD_IGNORES or (e.name.startswith(".") and e.name != ".env.example"):
                continue
            if len(entries) >= cap:
                more = True
                break
            entries.append(e)
    return entries, more


def _action_tree(target_dir: Path, depth: int = 2) -> str:
    if not target_dir.is_dir():
        return f"Error: Path '{target_dir}' is not a directory."

    depth = max(1, min(depth, 6))
    lines: list[str] = [f"{target_dir.name}/"]
    deadline = time.monotonic() + _TREE_MAX_SECONDS
    seen = [0]
    stopped: list[str] = []

    def _is_dir(e: os.DirEntry) -> bool:
        try:
            return e.is_dir()
        except OSError:
            return False

    def _walk(current: Path, prefix: str, current_depth: int):
        if current_depth > depth or stopped:
            return
        if time.monotonic() > deadline:
            stopped.append(f"[Truncated: tree stopped after {_TREE_MAX_SECONDS:g}s.]")
            return
        try:
            raw, more = _scan_dir_capped(current, _TREE_MAX_DIR_ENTRIES)
        except Exception:
            return
        seen[0] += len(raw)
        entries = sorted(raw, key=lambda x: (not _is_dir(x), x.name.lower()))

        total_entries = len(entries)
        max_entries = 35
        shown = entries[:max_entries]

        for i, entry in enumerate(shown):
            if stopped:
                return
            is_last = (i == len(shown) - 1) and (total_entries <= max_entries) and not more
            connector = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")
            entry_path = Path(entry.path)

            if _is_dir(entry):
                lines.append(f"{prefix}{connector}{entry.name}/")
                # Don't descend into symlinked directories that leave the project tree.
                if entry.is_symlink() and not _is_path_allowed(entry_path, get_allowed_roots()):
                    continue
                if seen[0] >= _TREE_MAX_TOTAL_ENTRIES:
                    stopped.append(f"[Truncated: tree stopped after {_TREE_MAX_TOTAL_ENTRIES} entries.]")
                    return
                _walk(entry_path, child_prefix, current_depth + 1)
            else:
                lines.append(f"{prefix}{connector}{entry.name}")

        if more:
            lines.append(
                f"{prefix}└── ... more items (listing stopped at {_TREE_MAX_DIR_ENTRIES} entries in this directory)"
            )
        elif total_entries > max_entries:
            lines.append(f"{prefix}└── ... and {total_entries - max_entries} more items")

    _walk(target_dir, "", 1)
    if stopped:
        lines.append(stopped[0])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Action 3: Read
# ---------------------------------------------------------------------------

def _action_read(target_file: Path, lines_arg: str | None = None) -> str:
    if not target_file.exists():
        return f"Error: File '{target_file}' does not exist."
    if target_file.is_dir():
        return f"Error: Path '{target_file.name}' is a directory. Use action='tree' to list directory contents."
    if not _is_regular_file(target_file):
        return f"Refused: '{target_file.name}' is not a regular file (FIFO, device or socket)."
    if is_secret_file(target_file):
        return (
            f"Refused: '{target_file.name}' is a credential/secret file (.env*, api_keys.json, *.pem, "
            "id_* keys, .git/*) and cannot be read through codebase."
        )
    if _is_binary(target_file):
        return f"Refused: File '{target_file.name}' is a binary file. Reading binary files is not supported."

    try:
        content, read_truncated = _read_capped(target_file)
    except Exception as e:
        return f"Error reading file '{target_file.name}': {e}"
    if read_truncated:
        # drop the possibly partial last line
        content = content.rsplit("\n", 1)[0]

    all_lines = content.splitlines()
    total_lines = len(all_lines)

    start_line = 1
    end_line = total_lines

    if lines_arg:
        arg = lines_arg.strip()
        m_range = re.match(r"^(\d+)\s*[-:]\s*(\d+)$", arg)
        m_single = re.match(r"^(\d+)$", arg)
        m_plus = re.match(r"^(\d+)\s*\+$", arg)

        if m_range:
            start_line = max(1, int(m_range.group(1)))
            end_line = min(total_lines, int(m_range.group(2)))
        elif m_single:
            idx = int(m_single.group(1))
            start_line = max(1, idx - 10)
            end_line = min(total_lines, idx + 10)
        elif m_plus:
            start_line = max(1, int(m_plus.group(1)))
            end_line = min(total_lines, start_line + 99)
        else:
            return f"Error: Invalid line range format '{lines_arg}'. Expected e.g. '120-180', '50', or '100+'."

    # Enforce safe read cap if no line range specified and file is long
    truncated_msg = ""
    if not lines_arg and total_lines > 100:
        end_line = 100
        truncated_msg = (
            f"\n\n[Truncated: showing lines 1-100 of {total_lines}. "
            f"Specify lines='101-{min(200, total_lines)}' to read further.]"
        )

    if start_line > total_lines:
        return f"Error: Start line {start_line} exceeds total file lines ({total_lines})."

    selected = all_lines[start_line - 1 : end_line]
    formatted: list[str] = [f"File: {target_file.name} (lines {start_line}-{end_line} of {total_lines}):"]
    for i, line in enumerate(selected, start=start_line):
        formatted.append(f"{i:4d} | {line}")

    if read_truncated:
        truncated_msg += (
            f"\n\n[File is larger than {_READ_MAX_BYTES} bytes: only the first {_READ_MAX_BYTES} bytes "
            "are readable through codebase.]"
        )
    return _redact("\n".join(formatted) + truncated_msg)


# ---------------------------------------------------------------------------
# Action 4: Find
# ---------------------------------------------------------------------------

def _action_find(target_dir: Path, query: str | None = None) -> str:
    pattern = (query or "*").strip()
    rg_path = shutil.which("rg")
    matches: list[Path] = []

    if rg_path:
        cmd = [
            rg_path,
            "--no-config",
            "--files",
            "--hidden",
            "-g", "!.git",
            "-g", "!node_modules",
            "-g", "!target",
            "-g", "!dist",
            "-g", "!build",
            "-g", "!.venv",
            "-g", "!__pycache__",
            "-g", pattern if ("*" in pattern or "?" in pattern) else f"*{pattern}*",
            *[arg for g in _SECRET_SEARCH_GLOBS for arg in ("--iglob", f"!{g}")],
            str(target_dir),
        ]
        try:
            out, _err, outcome, _rc = _run_rg_capped(cmd, timeout=8)
            if outcome == "capped" and "\n" in out:
                out = out.rsplit("\n", 1)[0]
            for l in out.splitlines():
                if l.strip():
                    matches.append(Path(l.strip()))
        except Exception:
            pass

    if not matches:
        # Fallback using glob / walk
        is_glob = any(c in pattern for c in ("*", "?", "["))
        p_lower = pattern.lower()
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in STANDARD_IGNORES and not d.startswith(".")]
            for fname in files:
                if is_glob:
                    if Path(fname).match(pattern):
                        matches.append(Path(root) / fname)
                else:
                    if p_lower in fname.lower():
                        matches.append(Path(root) / fname)

    if not matches:
        return f"No files found matching '{pattern}' in {target_dir.name}."

    dyn = _dynamic_secret_targets()
    matches = [m for m in matches if not is_secret_file(m) and not _is_dynamic_secret(m, dyn)]
    if not matches:
        return f"No files found matching '{pattern}' in {target_dir.name}."
    matches.sort(key=lambda p: str(p).lower())
    total = len(matches)
    shown = matches[:100]

    lines = [f"Found {total} files matching '{pattern}' in {target_dir.name}:"]
    for p in shown:
        try:
            rel = p.relative_to(target_dir)
            lines.append(f"  {rel}")
        except Exception:
            lines.append(f"  {p.name}")

    if total > 100:
        lines.append(f"\n[Truncated: showing first 100 of {total} files. Narrow pattern.]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Action 5: Outline
# ---------------------------------------------------------------------------

_RUST_RE = re.compile(
    r"^\s*(?:pub(?:\([^\)]+\))?\s+)?(?:async\s+)?(fn|struct|enum|trait|type|impl|const)\s+([a-zA-Z0-9_]+)",
)
_PYTHON_RE = re.compile(
    r"^(\s*)(?:async\s+)?(def|class)\s+([a-zA-Z0-9_]+)",
)
_TS_JS_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?(function|class|interface|type|enum|const)\s+([a-zA-Z0-9_]+)",
)
_GO_RE = re.compile(
    r"^\s*func\s+(?:\([^\)]+\)\s+)?([a-zA-Z0-9_]+)|^\s*type\s+([a-zA-Z0-9_]+)\s+(struct|interface)",
)
_SWIFT_RE = re.compile(
    r"^\s*(?:public\s+|open\s+|private\s+|internal\s+)?(?:final\s+)?(func|class|struct|protocol|enum|actor|extension)\s+([a-zA-Z0-9_]+)",
)


def _outline_file(file_path: Path) -> list[str]:
    ext = file_path.suffix.lower()
    if _is_binary(file_path):
        return []

    try:
        content, _ = _read_capped(file_path)
    except Exception:
        return []

    symbols: list[str] = []
    lines = content.splitlines()

    for idx, line in enumerate(lines, start=1):
        if ext == ".rs":
            m = _RUST_RE.match(line)
            if m:
                kw, name = m.group(1), m.group(2)
                symbols.append(f"  L{idx:4d}: {kw} {name}")
        elif ext == ".py":
            m = _PYTHON_RE.match(line)
            if m:
                indent, kw, name = len(m.group(1)), m.group(2), m.group(3)
                level = "    " if indent > 0 else "  "
                symbols.append(f"{level}L{idx:4d}: {kw} {name}")
        elif ext in (".ts", ".tsx", ".js", ".jsx"):
            m = _TS_JS_RE.match(line)
            if m:
                kw, name = m.group(1), m.group(2)
                symbols.append(f"  L{idx:4d}: {kw} {name}")
        elif ext == ".go":
            m = _GO_RE.match(line)
            if m:
                fn_name = m.group(1)
                type_name = m.group(2)
                type_kind = m.group(3)
                if fn_name:
                    symbols.append(f"  L{idx:4d}: func {fn_name}")
                elif type_name:
                    symbols.append(f"  L{idx:4d}: type {type_name} {type_kind}")
        elif ext == ".swift":
            m = _SWIFT_RE.match(line)
            if m:
                kw, name = m.group(1), m.group(2)
                symbols.append(f"  L{idx:4d}: {kw} {name}")

    return symbols


def _action_outline(target: Path) -> str:
    if target.is_file() and is_secret_file(target):
        return f"Refused: '{target.name}' is a credential/secret file and cannot be outlined."
    if target.is_file():
        syms = _outline_file(target)
        if not syms:
            return f"Outline for {target.name}: No top-level symbols detected or unsupported file type."
        return f"Outline for {target.name} ({len(syms)} symbols):\n" + "\n".join(syms)

    if target.is_dir():
        # Scan source files in directory (up to 25 files)
        outlines: list[str] = [f"Symbol outline for module {target.name}/:"]
        source_exts = {".rs", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".swift"}
        files_scanned = 0

        for root, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if d not in STANDARD_IGNORES and not d.startswith(".")]
            for fname in sorted(files):
                fpath = Path(root) / fname
                if is_secret_file(fpath) or is_secret_file(fpath.resolve()):
                    continue
                if fpath.is_symlink() and not _is_path_allowed(fpath.resolve(), get_allowed_roots()):
                    continue
                if fpath.suffix.lower() in source_exts:
                    syms = _outline_file(fpath)
                    if syms:
                        rel = fpath.relative_to(target)
                        outlines.append(f"\n[{rel}]")
                        outlines.extend(syms[:20])
                        if len(syms) > 20:
                            outlines.append(f"    ... and {len(syms)-20} more symbols")
                        files_scanned += 1
                        if files_scanned >= 20:
                            outlines.append(f"\n[Truncated: showing first 20 source files in module.]")
                            return "\n".join(outlines)

        if len(outlines) == 1:
            return f"No recognizable code symbols found in {target.name}/."
        return "\n".join(outlines)

    return f"Error: Target '{target.name}' not found."


# ---------------------------------------------------------------------------
# Action 6: Index & Action 7: Status
# ---------------------------------------------------------------------------

def _get_repo_git_info(repo_dir: Path) -> dict[str, Any]:
    git_dir = repo_dir / ".git"
    if not git_dir.exists():
        return {
            "is_git": False,
            "branch": "unversioned",
            "commit": "unversioned",
            "last_commit_msg": "",
            "last_commit_date": "",
            "dirty": False,
            "modified": 0,
            "untracked": 0,
        }

    info: dict[str, Any] = {"is_git": True}
    try:
        b_res = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2, env=git_safe_env(),
        )
        info["branch"] = b_res.stdout.strip() or "HEAD"

        c_res = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2, env=git_safe_env(),
        )
        info["commit"] = c_res.stdout.strip() or "unknown"

        log_res = subprocess.run(
            ["git", "-C", str(repo_dir), "log", "-1", "--no-ext-diff", "--no-textconv", "--format=%s (%cr by %an)"],
            capture_output=True, text=True, timeout=2, env=git_safe_env(),
        )
        info["last_commit"] = log_res.stdout.strip()

        st_res = subprocess.run(
            ["git", "-C", str(repo_dir), "status", "--porcelain"],
            capture_output=True, text=True, timeout=2, env=git_safe_env(),
        )
        lines = [l for l in st_res.stdout.splitlines() if l.strip()]
        info["dirty"] = len(lines) > 0
        info["modified"] = sum(1 for l in lines if not l.startswith("??"))
        info["untracked"] = sum(1 for l in lines if l.startswith("??"))
    except Exception:
        info["branch"] = "unknown"
        info["commit"] = "unknown"
        info["dirty"] = False
        info["modified"] = 0
        info["untracked"] = 0

    return info


# Limits for index/status walks so huge trees cannot keep the tool busy.
_WALK_MAX_FILES = 50_000
_WALK_MAX_SECONDS = 5.0


_WALK_MAX_DIR_ENTRIES = 10_000


def _count_repo_files(repo_dir: Path) -> tuple[int, dict[str, int], str]:
    """Counts files by extension with streaming os.scandir and early stop.

    Limits (checked per entry, before any directory is fully listed): total files
    _WALK_MAX_FILES, wall time _WALK_MAX_SECONDS, and _WALK_MAX_DIR_ENTRIES per
    directory. Returns (total, ext_counts, truncated_notice)."""
    total = 0
    ext_counts: dict[str, int] = {}
    deadline = time.monotonic() + _WALK_MAX_SECONDS
    notice = ""
    stack = [repo_dir]
    while stack and not notice:
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError:
            continue
        with it:
            per_dir = 0
            for e in it:
                if time.monotonic() > deadline:
                    notice = f"[Truncated: file walk stopped after {_WALK_MAX_SECONDS:g}s; counts are partial.]"
                    break
                per_dir += 1
                if per_dir > _WALK_MAX_DIR_ENTRIES:
                    notice = (
                        f"[Truncated: a directory has more than {_WALK_MAX_DIR_ENTRIES} entries; "
                        "counts are partial.]"
                    )
                    break
                name = e.name
                try:
                    if e.is_dir(follow_symlinks=False):
                        if name not in STANDARD_IGNORES and not name.startswith("."):
                            stack.append(Path(e.path))
                        continue
                except OSError:
                    continue
                if name.startswith("."):
                    continue
                if total >= _WALK_MAX_FILES:
                    notice = f"[Truncated: file walk stopped at {_WALK_MAX_FILES} files; counts are partial.]"
                    break
                total += 1
                ext = Path(name).suffix.lower() or "no_ext"
                ext_counts[ext] = ext_counts.get(ext, 0) + 1
    return total, ext_counts, notice


def _index_cache_path(repo_dir: Path) -> Path:
    repo_hash = hashlib.sha256(str(repo_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    return _get_cache_dir() / f"{repo_dir.name}_{repo_hash}.json"


def _action_index(repo_dir: Path) -> str:
    git_info = _get_repo_git_info(repo_dir)
    total_files, ext_counts, walk_notice = _count_repo_files(repo_dir)

    top_exts = sorted(ext_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    top_exts_str = ", ".join(f"{ext}: {count}" for ext, count in top_exts)

    now_iso = datetime.now(timezone.utc).isoformat()
    now_ts = time.time()

    index_data = {
        "repo_name": repo_dir.name,
        "path": str(repo_dir.resolve()),
        "commit": git_info.get("commit", "unversioned"),
        "branch": git_info.get("branch", "unversioned"),
        "indexed_at_iso": now_iso,
        "indexed_at_ts": now_ts,
        "file_count": total_files,
        "languages": ext_counts,
        "truncated": bool(walk_notice),
    }

    cache_file = _index_cache_path(repo_dir)
    try:
        cache_file.write_text(json.dumps(index_data, indent=2), encoding="utf-8")
    except Exception as e:
        return f"Warning: Index computed but failed to write cache file: {e}"

    commit_disp = git_info.get("commit", "unversioned")[:8]
    return (
        f"Indexed '{repo_dir.name}' at commit {commit_disp} (branch: {git_info.get('branch')}).\n"
        f"Total files: {total_files} ({top_exts_str}).\n"
        f"Index status: Fresh (snapshot saved)."
        + (f"\n{walk_notice}" if walk_notice else "")
    )


def _action_status(repo_dir: Path) -> str:
    git_info = _get_repo_git_info(repo_dir)
    total_files, ext_counts, walk_notice = _count_repo_files(repo_dir)
    top_exts = sorted(ext_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    top_exts_str = ", ".join(f"{ext}: {count}" for ext, count in top_exts)

    cache_file = _index_cache_path(repo_dir)
    freshness = "Not indexed yet (run codebase(action='index', root=...) to cache snapshot)."

    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            cached_commit = cached.get("commit", "")
            cached_ts = cached.get("indexed_at_ts", 0)
            elapsed_m = int((time.time() - cached_ts) / 60)

            current_commit = git_info.get("commit", "")
            if current_commit == cached_commit:
                freshness = f"Fresh (indexed {elapsed_m}m ago at commit {cached_commit[:8]})."
            else:
                freshness = (
                    f"Stale (snapshot at {cached_commit[:8]} from {elapsed_m}m ago; "
                    f"current HEAD is {current_commit[:8]})."
                )
        except Exception:
            pass

    dirty_desc = (
        f"DIRTY ({git_info.get('modified', 0)} modified, {git_info.get('untracked', 0)} untracked)"
        if git_info.get("dirty")
        else "Clean"
    )

    lines = [
        f"Repository: {repo_dir.name} ({repo_dir})",
        f"Branch: {git_info.get('branch', 'unknown')} | Status: {dirty_desc}",
        f"Last Commit: {git_info.get('last_commit', 'No commit history')}",
        f"Files: {total_files} ({top_exts_str})",
        f"Index Freshness: {freshness}",
    ]
    if walk_notice:
        lines.append(walk_notice)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main Handler & Discovery Specification
# ---------------------------------------------------------------------------

def codebase(parameters: dict | None = None, **kwargs) -> str:
    params = parameters or {}
    action = str(params.get("action", "")).strip().lower()

    if not action:
        return (
            "Error: 'action' parameter is required. "
            "Supported actions: 'search', 'tree', 'read', 'find', 'outline', 'index', 'status'."
        )

    allowed_roots = get_allowed_roots()
    root_arg = params.get("root")
    path_arg = params.get("path")

    repo_root, target_path, err = _resolve_target(root_arg, path_arg, allowed_roots)
    if err:
        return err

    target = target_path or repo_root
    if target is None:
        return "Error: Could not resolve target path or repository root."

    query = params.get("query")
    lines_arg = params.get("lines")
    depth_arg = params.get("depth")
    try:
        depth = int(depth_arg) if depth_arg is not None else None
    except (ValueError, TypeError):
        depth = None

    # Secret files (or anything under .git) are never a valid target for any action,
    # including symlinks that resolve to one.
    if is_secret_file(target) or (target_path is not None and is_secret_file(Path(os.path.expanduser(str(path_arg or ""))))):
        return (
            f"Refused: '{target.name}' is a credential/secret file (.env*, api_keys.json, *.pem, "
            "id_* keys, .git/*) and cannot be accessed through codebase."
        )

    if action == "search":
        return _action_search(target, query or "", depth=depth)
    elif action == "tree":
        return _action_tree(target, depth=depth or 2)
    elif action == "read":
        return _action_read(target, lines_arg=lines_arg)
    elif action == "find":
        return _action_find(target, query=query)
    elif action == "outline":
        return _action_outline(target)
    elif action == "index":
        return _action_index(repo_root or target)
    elif action == "status":
        return _action_status(repo_root or target)
    else:
        return (
            f"Error: codebase is strictly read-only. Action '{action}' is not supported. "
            "Supported actions: 'search', 'tree', 'read', 'find', 'outline', 'index', 'status'."
        )


TOOL = {
    "name": "codebase",
    "description": (
        "Ground-truth, read-only inspection tool for workstation repositories and project trees. "
        "Allows keyword/regex search (action='search'), directory tree listing (action='tree'), "
        "reading files with exact line ranges (action='read'), locating files by glob (action='find'), "
        "inspecting functions/classes/traits symbols (action='outline'), building index snapshots (action='index'), "
        "and checking branch/dirty/commit status (action='status'). Strictly root-scoped and read-only."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "The codebase action: 'search' (content search), 'tree' (directory tree), "
                    "'read' (read file or line range), 'find' (file glob search), "
                    "'outline' (symbol map of file or module), 'index' (build repo index), "
                    "or 'status' (repo overview & freshness)."
                ),
                "enum": ["search", "tree", "read", "find", "outline", "index", "status"],
            },
            "root": {
                "type": "STRING",
                "description": (
                    "Optional repository root or project name (e.g. 'kurekizmo', 'nodaysidle-flowstate', "
                    "or '~/dev/nodaysidle/kurekizmo')."
                ),
            },
            "path": {
                "type": "STRING",
                "description": "Specific file or subdirectory relative to root or as an absolute path within allowed roots.",
            },
            "query": {
                "type": "STRING",
                "description": "Search keyword/regex when action='search', or file name glob pattern when action='find'.",
            },
            "lines": {
                "type": "STRING",
                "description": "Line range when action='read' (e.g. '120-180', '50', or '100+'). Enforces safe reading.",
            },
            "depth": {
                "type": "INTEGER",
                "description": "Directory depth when action='tree' (default: 2, max: 6) or search depth when action='search'.",
            },
        },
        "required": ["action"],
    },
    "handler": codebase,
}
