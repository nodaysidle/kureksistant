"""Tests for actions/codebase.py — root scoping, search, tree, read, outline, index, and status."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from actions.codebase import codebase, get_allowed_roots, _is_path_allowed, _is_binary


@pytest.fixture
def test_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    ws_root = tmp_path / "dev" / "test_workspace"
    ws_root.mkdir(parents=True)

    # 1. Setup Rust crate fixture
    rust_crate = ws_root / "sample_rust"
    (rust_crate / "src").mkdir(parents=True)
    (rust_crate / "Cargo.toml").write_text(
        '[package]\nname = "sample_rust"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (rust_crate / "src" / "lib.rs").write_text(
        "pub struct WorkerConfig {\n"
        "    pub id: u32,\n"
        "}\n\n"
        "pub trait Runner {\n"
        "    fn execute(&self);\n"
        "}\n\n"
        "impl Runner for WorkerConfig {\n"
        "    fn execute(&self) {}\n"
        "}\n\n"
        "pub async fn start_worker() {}\n",
        encoding="utf-8",
    )

    # 2. Setup TypeScript project fixture
    ts_project = ws_root / "sample_ts"
    (ts_project / "src").mkdir(parents=True)
    (ts_project / "package.json").write_text(
        '{"name": "sample-ts", "version": "1.0.0"}\n',
        encoding="utf-8",
    )
    (ts_project / "src" / "index.ts").write_text(
        "export interface UserSession {\n"
        "  token: string;\n"
        "}\n\n"
        "export type Status = 'active' | 'idle';\n\n"
        "export class SessionManager {\n"
        "  constructor() {}\n"
        "}\n\n"
        "export function createSession(): UserSession {\n"
        "  return { token: 'abc' };\n"
        "}\n",
        encoding="utf-8",
    )

    # 3. Setup Python repo with git
    py_repo = ws_root / "sample_py"
    py_repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(py_repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(py_repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(py_repo), "config", "user.name", "Test User"], check=True)

    (py_repo / "main.py").write_text(
        'def main():\n    print("Hello world from python")\n\n'
        'class ServiceEngine:\n    def start(self):\n        pass\n',
        encoding="utf-8",
    )
    # Long file for read truncation testing
    long_content = "\n".join(f"line_{i} = {i}" for i in range(1, 150))
    (py_repo / "long_file.py").write_text(long_content, encoding="utf-8")

    # Binary file for rejection testing
    (py_repo / "data.bin").write_bytes(b"\x00\x01\x02\x03\xff\xfe")

    # Commit initial state
    subprocess.run(["git", "-C", str(py_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(py_repo), "commit", "-m", "Initial commit"], check=True)

    # Configure allowed roots in monkeypatch
    monkeypatch.setenv("KUREK_CODEBASE_ROOTS", str(ws_root))

    return {
        "workspace": ws_root,
        "rust": rust_crate,
        "ts": ts_project,
        "py": py_repo,
    }


def test_root_scoping_refusal(test_workspace: dict[str, Path], tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside_forbidden"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("classified", encoding="utf-8")

    result = codebase({"action": "read", "path": str(outside_dir / "secret.txt")})
    assert "Refused" in result
    assert "outside allowed project roots" in result


def test_search_speed_and_context(test_workspace: dict[str, Path]) -> None:
    py_repo = test_workspace["py"]

    start = time.perf_counter()
    result = codebase({"action": "search", "root": str(py_repo), "query": "Hello world"})
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"Search took {elapsed:.3f}s, expected under 1s"
    assert "Hello world from python" in result
    assert "main.py" in result


def test_search_empty_query_rejected(test_workspace: dict[str, Path]) -> None:
    py_repo = test_workspace["py"]
    result = codebase({"action": "search", "root": str(py_repo), "query": ""})
    assert "Error:" in result
    assert "query cannot be empty" in result.lower()


def test_tree_listing(test_workspace: dict[str, Path]) -> None:
    py_repo = test_workspace["py"]
    result = codebase({"action": "tree", "root": str(py_repo), "depth": 2})
    assert "sample_py/" in result
    assert "main.py" in result
    assert "long_file.py" in result
    assert ".git/" not in result  # .git is filtered


def test_read_full_and_ranges(test_workspace: dict[str, Path]) -> None:
    py_repo = test_workspace["py"]

    # Read small file
    res_full = codebase({"action": "read", "root": str(py_repo), "path": "main.py"})
    assert "File: main.py" in res_full
    assert "1 | def main():" in res_full
    assert "Hello world from python" in res_full

    # Read specific line range
    res_range = codebase({"action": "read", "root": str(py_repo), "path": "main.py", "lines": "3-5"})
    assert "lines 3-5" in res_range
    assert "class ServiceEngine:" in res_range

    # Read long file truncation safety
    res_long = codebase({"action": "read", "root": str(py_repo), "path": "long_file.py"})
    assert "Truncated: showing lines 1-100 of 149" in res_long
    assert "line_1 = 1" in res_long


def test_read_binary_rejected(test_workspace: dict[str, Path]) -> None:
    py_repo = test_workspace["py"]
    res = codebase({"action": "read", "root": str(py_repo), "path": "data.bin"})
    assert "Refused:" in res
    assert "binary file" in res.lower()


def test_find_by_glob(test_workspace: dict[str, Path]) -> None:
    ws = test_workspace["workspace"]
    res = codebase({"action": "find", "root": str(ws), "query": "*.rs"})
    assert "Found" in res
    assert "lib.rs" in res


def test_outline_rust_crate(test_workspace: dict[str, Path]) -> None:
    rust_crate = test_workspace["rust"]
    res = codebase({"action": "outline", "root": str(rust_crate), "path": "src/lib.rs"})
    assert "Outline for lib.rs" in res
    assert "struct WorkerConfig" in res
    assert "trait Runner" in res
    assert "impl Runner" in res
    assert "fn start_worker" in res


def test_outline_typescript_project(test_workspace: dict[str, Path]) -> None:
    ts_project = test_workspace["ts"]
    res = codebase({"action": "outline", "root": str(ts_project), "path": "src/index.ts"})
    assert "Outline for index.ts" in res
    assert "interface UserSession" in res
    assert "type Status" in res
    assert "class SessionManager" in res
    assert "function createSession" in res


def test_index_and_status(test_workspace: dict[str, Path]) -> None:
    py_repo = test_workspace["py"]

    # Initial status (before index)
    st1 = codebase({"action": "status", "root": str(py_repo)})
    assert "Repository: sample_py" in st1
    assert "Branch: main" in st1
    assert "Status: Clean" in st1

    # Run index
    idx_res = codebase({"action": "index", "root": str(py_repo)})
    assert "Indexed 'sample_py'" in idx_res
    assert "Index status: Fresh" in idx_res

    # Status after index should show Fresh
    st2 = codebase({"action": "status", "root": str(py_repo)})
    assert "Index Freshness: Fresh" in st2

    # Make dirty
    (py_repo / "new_untracked.txt").write_text("hello", encoding="utf-8")
    st3 = codebase({"action": "status", "root": str(py_repo)})
    assert "DIRTY" in st3
    assert "1 untracked" in st3


def test_strictly_read_only(test_workspace: dict[str, Path]) -> None:
    py_repo = test_workspace["py"]
    res = codebase({"action": "write", "root": str(py_repo), "path": "new.txt", "content": "bad"})
    assert "Error: codebase is strictly read-only" in res


def test_no_hardcoded_home_arch() -> None:
    root = Path(__file__).resolve().parents[1]
    source_file = root / "actions" / "codebase.py"
    text = source_file.read_text(encoding="utf-8")
    assert "/home/arch" not in text


# ---------------------------------------------------------------------------
# Regression tests: secret files are never readable/searchable via codebase
# ---------------------------------------------------------------------------

FAKE_GOOGLE = "AI" + "za" + "Q" * 35
FAKE_DS = "sk-" + "D7" * 15


@pytest.fixture
def secret_repo(test_workspace: dict[str, Path]) -> Path:
    repo = test_workspace["py"]
    (repo / ".env").write_text(f"DEEPSEEK_API_KEY={FAKE_DS}\nGEMINI_API_KEY={FAKE_GOOGLE}\n", encoding="utf-8")
    (repo / ".env.local").write_text(f"X={FAKE_DS}\n", encoding="utf-8")
    (repo / ".env.example").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
    (repo / "api_keys.json").write_text(f'{{"deepseek": "{FAKE_DS}"}}\n', encoding="utf-8")
    (repo / "config").mkdir()
    (repo / "config" / "api_keys.json").write_text(f'{{"gemini": "{FAKE_GOOGLE}"}}\n', encoding="utf-8")
    (repo / "server.pem").write_text(f"{FAKE_DS}\n", encoding="utf-8")
    (repo / "id_ed25519").write_text(f"{FAKE_DS}\n", encoding="utf-8")
    (repo / "innocent.txt").symlink_to(repo / ".env")
    return repo


@pytest.mark.parametrize("rel", [
    ".env", ".env.local", "api_keys.json", "config/api_keys.json", "server.pem",
    "id_ed25519", ".git/config", "innocent.txt",
])
def test_codebase_read_secret_files_refused(secret_repo: Path, rel: str) -> None:
    res = codebase({"action": "read", "root": str(secret_repo), "path": rel})
    assert res.startswith("Refused"), (rel, res)
    assert FAKE_DS not in res and FAKE_GOOGLE not in res


def test_codebase_env_example_still_readable(secret_repo: Path) -> None:
    res = codebase({"action": "read", "root": str(secret_repo), "path": ".env.example"})
    assert "DEEPSEEK_API_KEY=" in res and not res.startswith("Refused")


def test_codebase_search_skips_secret_files(secret_repo: Path) -> None:
    for q in (FAKE_DS, FAKE_GOOGLE):
        res = codebase({"action": "search", "root": str(secret_repo), "query": q})
        assert res.startswith("No matches found"), res
    res = codebase({"action": "search", "root": str(secret_repo), "query": "DEEPSEEK_API_KEY"})
    assert FAKE_DS not in res and FAKE_GOOGLE not in res
    for name in (".env:", ".env.local", "api_keys.json", "server.pem", "id_ed25519", "innocent.txt"):
        assert name not in res, (name, res)


def test_codebase_read_redacts_incidental_secrets(test_workspace: dict[str, Path]) -> None:
    repo = test_workspace["py"]
    (repo / "settings.py").write_text(f'GOOGLE_KEY = "{FAKE_GOOGLE}"\nname = "kurek"\n', encoding="utf-8")
    res = codebase({"action": "read", "root": str(repo), "path": "settings.py"})
    assert FAKE_GOOGLE not in res
    assert "[REDACTED_GOOGLE_KEY]" in res
    assert 'name = "kurek"' in res


def test_codebase_search_python_fallback_skips_secrets(secret_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import actions.codebase as cb
    monkeypatch.setattr(cb.shutil, "which", lambda name: None)
    res = codebase({"action": "search", "root": str(secret_repo), "query": "API_KEY"})
    assert FAKE_DS not in res and FAKE_GOOGLE not in res
    assert "innocent.txt" not in res
