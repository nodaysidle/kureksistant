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
                    if direct.is_dir():
                        matched.append(direct)
                    else:
                        # Scan one level down (e.g. ~/dev/nodaysidle/kurekizmo)
                        try:
                            for sub in ar.iterdir():
                                if sub.is_dir():
                                    sub_target = (sub / r_str).resolve()
                                    if sub_target.is_dir():
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


def _is_binary(file_path: Path) -> bool:
    """Quickly check if file is binary by extension or null byte in first 1024 bytes."""
    if file_path.suffix.lower() in BINARY_EXTENSIONS:
        return True
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

    if rg_path:
        cmd = [
            rg_path,
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
        ]
        if depth is not None and depth > 0:
            cmd.extend(["--max-depth", str(depth)])
        cmd.extend(["-e", query_str, str(target_dir)])

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            raw_out = res.stdout.strip()
            if not raw_out and res.returncode == 1:
                return f"No matches found for '{query_str}' in {target_dir.name}."
            if not raw_out and res.stderr:
                return f"Search notice: {res.stderr.strip()}"
            lines = raw_out.splitlines()
        except subprocess.TimeoutExpired:
            return f"Search timed out after 12s. Narrow query or specify a subpath."
        except Exception as e:
            lines = []
    else:
        # Fallback pure Python search
        lines = []
        try:
            pattern = re.compile(re.escape(query_str), re.IGNORECASE)
            for root, dirs, files in os.walk(target_dir):
                dirs[:] = [d for d in dirs if d not in STANDARD_IGNORES and not d.startswith(".")]
                for fname in sorted(files):
                    fpath = Path(root) / fname
                    if _is_binary(fpath):
                        continue
                    try:
                        content_lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
                        for idx, l in enumerate(content_lines):
                            if pattern.search(l):
                                rel = fpath.relative_to(target_dir)
                                lines.append(f"{rel}:{idx+1}:{l}")
                    except Exception:
                        pass
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

    return header + body + footer


# ---------------------------------------------------------------------------
# Action 2: Tree
# ---------------------------------------------------------------------------

def _action_tree(target_dir: Path, depth: int = 2) -> str:
    if not target_dir.is_dir():
        return f"Error: Path '{target_dir}' is not a directory."

    depth = max(1, min(depth, 6))
    lines: list[str] = [f"{target_dir.name}/"]

    def _walk(current: Path, prefix: str, current_depth: int):
        if current_depth > depth:
            return
        try:
            entries = sorted(
                [e for e in current.iterdir() if e.name not in STANDARD_IGNORES and not (e.name.startswith(".") and e.name != ".env.example")],
                key=lambda x: (not x.is_dir(), x.name.lower())
            )
        except Exception:
            return

        total_entries = len(entries)
        max_entries = 35
        shown = entries[:max_entries]

        for i, entry in enumerate(shown):
            is_last = (i == len(shown) - 1) and (total_entries <= max_entries)
            connector = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")

            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                _walk(entry, child_prefix, current_depth + 1)
            else:
                lines.append(f"{prefix}{connector}{entry.name}")

        if total_entries > max_entries:
            lines.append(f"{prefix}└── ... and {total_entries - max_entries} more items")

    _walk(target_dir, "", 1)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Action 3: Read
# ---------------------------------------------------------------------------

def _action_read(target_file: Path, lines_arg: str | None = None) -> str:
    if not target_file.exists():
        return f"Error: File '{target_file}' does not exist."
    if target_file.is_dir():
        return f"Error: Path '{target_file.name}' is a directory. Use action='tree' to list directory contents."
    if _is_binary(target_file):
        return f"Refused: File '{target_file.name}' is a binary file. Reading binary files is not supported."

    try:
        content = target_file.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Error reading file '{target_file.name}': {e}"

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

    return "\n".join(formatted) + truncated_msg


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
            str(target_dir),
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            for l in res.stdout.splitlines():
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
        content = file_path.read_text(encoding="utf-8", errors="replace")
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
            capture_output=True, text=True, timeout=2
        )
        info["branch"] = b_res.stdout.strip() or "HEAD"

        c_res = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2
        )
        info["commit"] = c_res.stdout.strip() or "unknown"

        log_res = subprocess.run(
            ["git", "-C", str(repo_dir), "log", "-1", "--format=%s (%cr by %an)"],
            capture_output=True, text=True, timeout=2
        )
        info["last_commit"] = log_res.stdout.strip()

        st_res = subprocess.run(
            ["git", "-C", str(repo_dir), "status", "--porcelain"],
            capture_output=True, text=True, timeout=2
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


def _count_repo_files(repo_dir: Path) -> tuple[int, dict[str, int]]:
    """Counts files and aggregates counts by language/extension."""
    total = 0
    ext_counts: dict[str, int] = {}
    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in STANDARD_IGNORES and not d.startswith(".")]
        for fname in files:
            if fname.startswith("."):
                continue
            total += 1
            ext = Path(fname).suffix.lower() or "no_ext"
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
    return total, ext_counts


def _index_cache_path(repo_dir: Path) -> Path:
    repo_hash = hashlib.sha256(str(repo_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    return _get_cache_dir() / f"{repo_dir.name}_{repo_hash}.json"


def _action_index(repo_dir: Path) -> str:
    git_info = _get_repo_git_info(repo_dir)
    total_files, ext_counts = _count_repo_files(repo_dir)

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
    )


def _action_status(repo_dir: Path) -> str:
    git_info = _get_repo_git_info(repo_dir)
    total_files, ext_counts = _count_repo_files(repo_dir)
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
