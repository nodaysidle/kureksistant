"""
actions/workstation_radar.py — Git workstation health radar for Kurek.
Auto-discovered by core/action_loader.py.

Capabilities:
  • Scans all repositories across ~/Projects and ~/dev in parallel.
  • Detects dirty working trees (modified/untracked files), unpushed commits ahead of upstream, and stashes.
  • Returns a high-density executive voice summary and structured markdown audit.
"""
from __future__ import annotations

import concurrent.futures
import subprocess
from pathlib import Path

WORKSPACE_ROOTS = [
    Path.home() / "Projects",
    Path.home() / "dev",
]


def _check_single_repo(repo_path: Path) -> dict | None:
    try:
        # 1. Branch name
        branch_res = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2
        )
        branch = branch_res.stdout.strip() or "HEAD"

        # 2. Status porcelain
        status_res = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=3
        )
        lines = [l for l in status_res.stdout.splitlines() if l.strip()]
        modified_count = sum(1 for l in lines if not l.startswith("??"))
        untracked_count = sum(1 for l in lines if l.startswith("??"))
        is_dirty = len(lines) > 0

        # 3. Unpushed commits ahead of upstream
        ahead_count = 0
        upstream_res = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--abbrev-ref", "@{u}"],
            capture_output=True, text=True, timeout=2
        )
        if upstream_res.returncode == 0:
            log_res = subprocess.run(
                ["git", "-C", str(repo_path), "log", "@{u}..HEAD", "--oneline"],
                capture_output=True, text=True, timeout=2
            )
            ahead_count = len([l for l in log_res.stdout.splitlines() if l.strip()])

        # 4. Stashes
        stash_res = subprocess.run(
            ["git", "-C", str(repo_path), "stash", "list"],
            capture_output=True, text=True, timeout=2
        )
        stash_count = len([l for l in stash_res.stdout.splitlines() if l.strip()])

        return {
            "name": repo_path.name,
            "path": str(repo_path),
            "branch": branch,
            "is_dirty": is_dirty,
            "modified": modified_count,
            "untracked": untracked_count,
            "ahead": ahead_count,
            "stashes": stash_count,
        }
    except Exception:
        return None


def workstation_radar(parameters: dict | None = None, **kwargs) -> str:
    params = parameters or {}
    filter_mode = params.get("filter", "actionable").lower()  # 'actionable' (dirty/unpushed) or 'all'

    repo_dirs: list[Path] = []
    for root in WORKSPACE_ROOTS:
        if not root.exists() or not root.is_dir():
            continue
        try:
            for p in root.iterdir():
                if p.is_dir() and (p / ".git").exists():
                    repo_dirs.append(p)
        except Exception:
            pass

    if not repo_dirs:
        return "No git repositories found in ~/Projects or ~/dev."

    # Scan all repositories in parallel
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(_check_single_repo, r): r for r in repo_dirs}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                results.append(res)

    results.sort(key=lambda x: x["name"].lower())

    dirty_repos = [r for r in results if r["is_dirty"]]
    unpushed_repos = [r for r in results if r["ahead"] > 0]
    stashed_repos = [r for r in results if r["stashes"] > 0]
    clean_count = len(results) - len(dirty_repos)

    # Executive Spoken Summary (Jarvis style)
    summary_parts = [f"Scanned {len(results)} repositories across your workspace."]
    if dirty_repos:
        names = ", ".join(r["name"] for r in dirty_repos[:4])
        more = f" and {len(dirty_repos) - 4} others" if len(dirty_repos) > 4 else ""
        summary_parts.append(f"{len(dirty_repos)} dirty ({names}{more}).")
    else:
        summary_parts.append("Zero dirty repositories.")

    if unpushed_repos:
        names = ", ".join(f"{r['name']} ({r['ahead']} ahead)" for r in unpushed_repos[:3])
        summary_parts.append(f"{len(unpushed_repos)} with unpushed commits: {names}.")
    else:
        summary_parts.append("All commits pushed to remotes.")

    voice_summary = " ".join(summary_parts)

    # Detailed Markdown Audit
    report_lines = [
        f"### 📡 Git Workstation Radar ({len(results)} repos total)",
        f"**Status:** {voice_summary}\n",
    ]

    if dirty_repos:
        report_lines.append("#### ⚠️ Repositories With Uncommitted Changes:")
        for r in dirty_repos:
            details = []
            if r["modified"]:
                details.append(f"{r['modified']} modified")
            if r["untracked"]:
                details.append(f"{r['untracked']} untracked")
            report_lines.append(f"- **{r['name']}** (`{r['branch']}`): {', '.join(details)}")
        report_lines.append("")

    if unpushed_repos:
        report_lines.append("#### 🚀 Repositories Ahead of Remote (Unpushed):")
        for r in unpushed_repos:
            report_lines.append(f"- **{r['name']}** (`{r['branch']}`): {r['ahead']} commit(s) ahead")
        report_lines.append("")

    if stashed_repos:
        report_lines.append("#### 📦 Repositories With Stashes:")
        for r in stashed_repos:
            report_lines.append(f"- **{r['name']}**: {r['stashes']} stash(es)")
        report_lines.append("")

    report_lines.append(f"✅ **{clean_count} repositories are 100% clean and synced.**")

    return "\n".join(report_lines)


TOOL = {
    "name": "workstation_radar",
    "description": "Scans all repositories in ~/Projects and ~/dev in parallel. Reports repositories with uncommitted changes, modified or untracked files, and unpushed commits ahead of upstream remotes.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "filter": {
                "type": "STRING",
                "description": "Report mode: 'actionable' (default, highlights only dirty or unpushed repos) or 'all'",
                "enum": ["actionable", "all"]
            }
        }
    },
    "handler": workstation_radar,
}
