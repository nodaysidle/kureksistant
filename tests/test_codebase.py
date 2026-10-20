"""Tests for actions/codebase.py — root scoping, search, tree, read, outline, index, and status."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from actions.codebase import codebase


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


def test_codebase_root_traversal_refused(test_workspace: dict[str, Path], tmp_path: Path) -> None:
    outside = tmp_path / "outside_root_dir"
    outside.mkdir()
    (outside / "x.txt").write_text("OUTSIDE_ROOT_CONTENT", encoding="utf-8")
    for root in ("../../outside_root_dir", "../outside_root_dir", str(outside)):
        for action in ("tree", "search", "find"):
            res = codebase({"action": action, "root": root, "query": "OUTSIDE"})
            assert "OUTSIDE_ROOT_CONTENT" not in res and "x.txt" not in res, (root, action, res)


@pytest.mark.parametrize("name", [".pgpass", "credentials.json", "API_KEYS.JSON", "cert.p12"])
def test_codebase_search_and_read_skip_extra_secret_names(secret_repo: Path, name: str) -> None:
    (secret_repo / name).write_text(f"value={FAKE_DS}\n", encoding="utf-8")
    res = codebase({"action": "search", "root": str(secret_repo), "query": "value="})
    assert FAKE_DS not in res and name not in res
    assert codebase({"action": "read", "root": str(secret_repo), "path": name}).startswith("Refused")
    assert codebase({"action": "outline", "root": str(secret_repo), "path": name}).startswith("Refused")


@pytest.mark.parametrize("rel", [".env", "api_keys.json", "innocent.txt", ".git/config", "config/api_keys.json"])
@pytest.mark.parametrize("action", ["search", "find", "outline", "tree", "read"])
def test_round3_secret_file_as_direct_target_refused(secret_repo: Path, rel: str, action: str) -> None:
    res = codebase({"action": action, "root": str(secret_repo), "path": rel, "query": "KEY"})
    assert res.startswith("Refused"), (action, rel, res)
    assert FAKE_DS not in res and FAKE_GOOGLE not in res


def test_round3_find_hides_secret_names(secret_repo: Path) -> None:
    res = codebase({"action": "find", "root": str(secret_repo), "query": "*"})
    listed = {line.strip().rsplit("/", 1)[-1] for line in res.splitlines()[1:] if line.strip()}
    # real secrets and the symlink to .env must not be listed; templates may be (owner decision)
    for name in (".env", ".env.local", "api_keys.json", "server.pem", "id_ed25519", "innocent.txt"):
        assert name not in listed, (name, res)
    assert "main.py" in listed
    assert all(n in _TEMPLATE_NAMES or not n.startswith(".env") for n in listed), res


_TEMPLATE_NAMES = {".env.example", ".env.sample", ".env.template"}


def test_r10_find_fallback_without_rg_hides_secrets_and_symlinks(secret_repo: Path, monkeypatch: pytest.MonkeyPatch,
                                                                tmp_path: Path) -> None:
    import actions.codebase as cb
    monkeypatch.setattr(cb.shutil, "which", lambda name: None)  # force the no-rg fallback
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("x\n", encoding="utf-8")
    (secret_repo / "elsewhere.txt").symlink_to(outside)
    for q in ("*", "innocent", ".env", "txt", "elsewhere"):
        res = codebase({"action": "find", "root": str(secret_repo), "query": q})
        listed = {line.strip().rsplit("/", 1)[-1] for line in res.splitlines()[1:] if line.strip()}
        for name in (".env", ".env.local", "api_keys.json", "server.pem", "id_ed25519", "innocent.txt",
                     "elsewhere.txt"):
            assert name not in listed, (q, name, res)
        assert FAKE_DS not in res and FAKE_GOOGLE not in res
    res = codebase({"action": "find", "root": str(secret_repo), "query": "*"})
    assert "main.py" in res and ".env.example" in res
    res = codebase({"action": "search", "root": str(secret_repo), "query": FAKE_DS[:12]})
    assert FAKE_DS not in res


def test_round3_tree_does_not_descend_symlinked_outside_dir(test_workspace: dict[str, Path], tmp_path: Path) -> None:
    outside = tmp_path / "outside_tree"
    outside.mkdir()
    (outside / "hidden_outside_name.txt").write_text("x", encoding="utf-8")
    repo = test_workspace["py"]
    (repo / "linkdir").symlink_to(outside, target_is_directory=True)
    res = codebase({"action": "tree", "root": str(repo), "depth": 3})
    assert "linkdir/" in res
    assert "hidden_outside_name.txt" not in res


# ---------------------------------------------------------------------------
# Round 4 regression tests: (b) safe git env for status, (c) fallback symlinks
# ---------------------------------------------------------------------------


def test_b_codebase_status_does_not_run_repo_fsmonitor(test_workspace: dict[str, Path], tmp_path: Path) -> None:
    repo = test_workspace["py"]
    marker = tmp_path / "fsmonitor_ran"
    hook = tmp_path / "fsmon.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "config", "core.fsmonitor", str(hook)], check=True)
    # sanity: without the overrides git really would run it
    subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True)
    assert marker.exists()
    marker.unlink()
    res = codebase({"action": "status", "root": str(repo)})
    assert "main" in res
    assert not marker.exists()


def test_b_git_safe_env_overrides() -> None:
    from actions.codebase import git_safe_env
    env = git_safe_env({"GIT_EXTERNAL_DIFF": "x", "PATH": "/usr/bin"})
    assert "GIT_EXTERNAL_DIFF" not in env
    pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert pairs == {"core.fsmonitor": "false", "core.pager": "cat", "core.hooksPath": os.devnull,
                     "diff.external": "", "credential.helper": ""}


def test_c_fallback_search_skips_symlinks_out_of_roots(test_workspace: dict[str, Path], tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    import actions.codebase as cb
    outside = tmp_path / "outside_fallback.txt"
    outside.write_text("OUTSIDE_FALLBACK_MARKER\n", encoding="utf-8")
    repo = test_workspace["py"]
    (repo / "looks_local.txt").symlink_to(outside)
    monkeypatch.setattr(cb.shutil, "which", lambda name: None)
    res = codebase({"action": "search", "root": str(repo), "query": "OUTSIDE_FALLBACK_MARKER"})
    assert res.startswith("No matches found"), res
    assert "looks_local.txt" not in res


def test_followup_git_safe_env_scrubs_repo_redirection() -> None:
    from actions.codebase import git_safe_env
    env = git_safe_env({"GIT_DIR": "/elsewhere/.git", "GIT_WORK_TREE": "/elsewhere",
                        "GIT_INDEX_FILE": "x", "GIT_OBJECT_DIRECTORY": "x",
                        "GIT_ALTERNATE_OBJECT_DIRECTORIES": "x", "GIT_COMMON_DIR": "x"})
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
              "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR"):
        assert k not in env


@pytest.mark.parametrize("name", [".env_local", ".env-secret", ".envlocal", "id_ed25519_sk", "id_rsa_backup"])
def test_followup_more_secret_name_variants(secret_repo: Path, name: str) -> None:
    (secret_repo / name).write_text(f"K={FAKE_DS}\n", encoding="utf-8")
    assert codebase({"action": "read", "root": str(secret_repo), "path": name}).startswith("Refused")
    res = codebase({"action": "search", "root": str(secret_repo), "query": "K="})
    assert FAKE_DS not in res and name not in res


def test_followup_outline_dir_skips_secret_and_outside_symlinks(test_workspace: dict[str, Path], tmp_path: Path) -> None:
    repo = test_workspace["py"]
    outside = tmp_path / "outside_outline.py"
    outside.write_text("def outside_only_symbol():\n    pass\n", encoding="utf-8")
    (repo / "linked.py").symlink_to(outside)
    res = codebase({"action": "outline", "root": str(repo), "path": "."})
    assert "outside_only_symbol" not in res
    assert "ServiceEngine" in res



# ---------------------------------------------------------------------------
# Round 5 regression tests: (1) rg config, (5) regular files only
# ---------------------------------------------------------------------------


def test_r5_1_codebase_search_ignores_ripgrep_config(test_workspace: dict[str, Path], tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    repo = test_workspace["py"]
    cfg = tmp_path / "rgrc"
    cfg.write_text("--follow\n--pre=rm\n", encoding="utf-8")
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", str(cfg))
    res = codebase({"action": "search", "root": str(repo), "query": "Hello world"})
    assert "main.py" in res
    assert (repo / "main.py").exists()
    res = codebase({"action": "find", "root": str(repo), "query": "main"})
    assert "main.py" in res and (repo / "main.py").exists()


def test_r5_5_codebase_read_refuses_fifo(test_workspace: dict[str, Path]) -> None:
    import threading
    repo = test_workspace["py"]
    fifo = repo / "pipe.txt"
    os.mkfifo(fifo)
    out: dict[str, str] = {}
    th = threading.Thread(target=lambda: out.setdefault("r", codebase({"action": "read", "root": str(repo), "path": "pipe.txt"})), daemon=True)
    th.start()
    th.join(5)
    assert not th.is_alive(), "codebase read blocked on a FIFO"
    assert out["r"].startswith("Refused") and "regular file" in out["r"]
    # search/outline over the directory must not block on it either
    th2 = threading.Thread(target=lambda: out.setdefault("s", codebase({"action": "outline", "root": str(repo), "path": "."})), daemon=True)
    th2.start()
    th2.join(5)
    assert not th2.is_alive()


def test_r5_5_codebase_read_refuses_device(test_workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    repo = test_workspace["py"]
    (repo / "devnull_link").symlink_to("/dev/null")
    monkeypatch.setenv("KUREK_CODEBASE_ROOTS", f"{test_workspace['workspace']}:/dev")
    res = codebase({"action": "read", "root": str(repo), "path": "devnull_link"})
    assert res.startswith("Refused") and "regular file" in res


# ---------------------------------------------------------------------------
# Round 7 regression tests: (1) registry auth files, (2) byte caps
# ---------------------------------------------------------------------------

_REG_AUTH_VALUE = "cmVn" + "QXV0aFZhbHVl" + "OTk="
_REG_AUTH_FILES = [
    ".docker/config.json", ".config/containers/auth.json", "run/containers/auth.json",
    ".dockercfg", ".npmrc", ".pypirc", ".cargo/credentials.toml", ".gem/credentials",
]


@pytest.fixture
def registry_repo(test_workspace: dict[str, Path]) -> Path:
    repo = test_workspace["py"]
    for rel in _REG_AUTH_FILES:
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('{"auths": {"ghcr.io": {"auth": "%s"}}}\n' % _REG_AUTH_VALUE, encoding="utf-8")
    return repo


@pytest.mark.parametrize("rel", _REG_AUTH_FILES)
def test_r7_1_registry_auth_files_refused(registry_repo: Path, rel: str) -> None:
    from actions.codebase import is_secret_file
    assert is_secret_file(Path(rel))
    res = codebase({"action": "read", "root": str(registry_repo), "path": rel})
    assert res.startswith("Refused"), (rel, res)
    assert _REG_AUTH_VALUE not in res


def test_r7_1_registry_auth_hidden_from_search_and_find(registry_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for action, q in (("search", "auths"), ("find", "*.json"), ("find", "config")):
        res = codebase({"action": action, "root": str(registry_repo), "path": ".", "query": q})
        assert _REG_AUTH_VALUE not in res, (action, res)
        assert "docker/config.json" not in res and "containers/auth.json" not in res, (action, res)
    import actions.codebase as cb
    monkeypatch.setattr(cb.shutil, "which", lambda name: None)  # python fallback
    res = codebase({"action": "search", "root": str(registry_repo), "path": ".", "query": "auths"})
    assert _REG_AUTH_VALUE not in res


def test_r7_1_registry_auth_file_env_override(test_workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from actions.codebase import is_secret_file
    repo = test_workspace["py"]
    custom = repo / "my-reg-auth.json"
    custom.write_text('{"auths": {}}\n', encoding="utf-8")
    monkeypatch.setenv("REGISTRY_AUTH_FILE", str(custom))
    assert is_secret_file(custom)
    dcfg = repo / "dockercfgdir"
    dcfg.mkdir()
    (dcfg / "config.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("DOCKER_CONFIG", str(dcfg))
    assert is_secret_file(dcfg / "config.json")
    assert not is_secret_file(repo / "package.json")


def test_r7_1_plain_config_json_still_readable(test_workspace: dict[str, Path]) -> None:
    repo = test_workspace["py"]
    (repo / "config.json").write_text('{"name": "demo"}\n', encoding="utf-8")
    res = codebase({"action": "read", "root": str(repo), "path": "config.json"})
    assert "demo" in res and not res.startswith("Refused")


def test_r7_1_registry_values_redacted() -> None:
    from actions.run import _redact_secrets
    text = ('{"auths": {"a": {"auth": "dXNlcjpwYXNz"}, "b": {"identitytoken": "abc", '
            '"registrytoken": "def", "password": "hunter2"}}}\n'
            "//registry.npmjs.org/:_authToken=npm_abcdef\n_auth=Zm9vOmJhcg==\n")
    out = _redact_secrets(text)
    for v in ("dXNlcjpwYXNz", '"abc"', '"def"', "hunter2", "npm_abcdef", "Zm9vOmJhcg=="):
        assert v not in out, v
    code = "auth = get_auth()\npassword: str = field()\nif auth:\n"
    assert _redact_secrets(code) == code


def test_r7_2_read_never_reads_whole_file(test_workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import actions.codebase as cb
    repo = test_workspace["py"]
    big = repo / "big.txt"
    big.write_text("x" * 99 + "\n" * 1, encoding="utf-8")
    with open(big, "a", encoding="utf-8") as f:
        for _ in range(30000):
            f.write("y" * 99 + "\n")
    monkeypatch.setattr(cb, "_READ_MAX_BYTES", 50_000)
    sizes: list[int] = []
    real_open = builtins.open

    class _Spy:
        def __init__(self, fh):
            self._fh = fh
        def read(self, n=-1):
            sizes.append(n)
            return self._fh.read(n)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            self._fh.close()
        def __getattr__(self, k):
            return getattr(self._fh, k)

    def spy_open(path, mode="r", *a, **kw):
        fh = real_open(path, mode, *a, **kw)
        return _Spy(fh) if str(path) == str(big) and "b" in mode else fh

    monkeypatch.setattr(builtins, "open", spy_open)
    res = codebase({"action": "read", "root": str(repo), "path": "big.txt"})
    monkeypatch.setattr(builtins, "open", real_open)
    assert "larger than 50000 bytes" in res, res[-300:]
    reads = [n for n in sizes if n != 1024]  # _is_binary probe
    assert reads and all(0 < n <= 50_001 for n in reads), sizes
    res2 = codebase({"action": "read", "root": str(repo), "path": "big.txt", "lines": "29000-29010"})
    assert res2.startswith("Error") or "y" * 99 not in res2


def test_r7_2_search_output_capped_and_rg_group_killed(test_workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    import actions.codebase as cb
    if not shutil.which("rg"):
        pytest.skip("rg not installed")
    repo = test_workspace["py"]
    for i in range(20):
        (repo / f"many_{i}.txt").write_text("needle line\n" * 2000, encoding="utf-8")
    monkeypatch.setattr(cb, "_SEARCH_MAX_BYTES", 8192)
    seen: dict[str, object] = {}
    real = cb._run_rg_capped

    def spy(cmd, timeout):
        out, err, outcome, rc = real(cmd, timeout)
        seen["outcome"], seen["bytes"] = outcome, len(out.encode()) + len(err.encode())
        return out, err, outcome, rc

    monkeypatch.setattr(cb, "_run_rg_capped", spy)
    res = codebase({"action": "search", "root": str(repo), "path": ".", "query": "needle"})
    assert seen["outcome"] == "capped" and seen["bytes"] <= 8192
    assert "Output limit" in res


def test_r7_2_rg_capped_kills_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    import actions.codebase as cb
    monkeypatch.setattr(cb, "_SEARCH_MAX_BYTES", 4096)
    started: dict[str, object] = {}
    real_popen = cb.subprocess.Popen

    def popen(*a, **kw):
        p = real_popen(*a, **kw)
        started["p"], started["kw"] = p, kw
        return p

    monkeypatch.setattr(cb.subprocess, "Popen", popen)
    out, err, outcome, _ = cb._run_rg_capped(["sh", "-c", "yes kurek & yes kurek; wait"], timeout=30)
    assert outcome == "capped" and len(out.encode()) + len(err.encode()) <= 4096
    assert started["kw"]["start_new_session"] is True
    p = started["p"]
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.killpg(p.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("rg process group still alive after cap")


def test_r7_2_fallback_search_capped(test_workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    import actions.codebase as cb
    repo = test_workspace["py"]
    for i in range(10):
        (repo / f"fb_{i}.txt").write_text("needle line\n" * 2000, encoding="utf-8")
    monkeypatch.setattr(cb.shutil, "which", lambda name: None)
    monkeypatch.setattr(cb, "_SEARCH_MAX_BYTES", 8192)
    res = codebase({"action": "search", "root": str(repo), "path": ".", "query": "needle"})
    assert "Output limit" in res


# ---------------------------------------------------------------------------
# Round 8 regression tests: (2) dynamic registry auth targets, (3) walk limits
# ---------------------------------------------------------------------------

_R8_VAL = "r8" + "DynAuth" + "Value55"


def test_r8_2_custom_registry_auth_file_excluded_from_search_and_find(test_workspace: dict[str, Path],
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    repo = test_workspace["py"]
    custom = repo / "my-registry-login.json"
    custom.write_text(f'{{"auths": {{"x": {{"value": "{_R8_VAL}"}}}}}}\n', encoding="utf-8")
    dcfg = repo / "dockerstuff"
    dcfg.mkdir()
    (dcfg / "config.json").write_text(f'{{"other": "{_R8_VAL}"}}\n', encoding="utf-8")
    monkeypatch.setenv("REGISTRY_AUTH_FILE", str(custom))
    monkeypatch.setenv("DOCKER_CONFIG", str(dcfg))
    for action, q in (("search", _R8_VAL), ("find", "*.json"), ("find", "registry")):
        res = codebase({"action": action, "root": str(repo), "path": ".", "query": q})
        body = res.split("\n", 1)[1] if "\n" in res else ""
        assert _R8_VAL not in body and "my-registry-login.json" not in res, (action, res)
        assert "dockerstuff/config.json" not in res, (action, res)
    import actions.codebase as cb
    monkeypatch.setattr(cb.shutil, "which", lambda name: None)
    res = codebase({"action": "search", "root": str(repo), "path": ".", "query": _R8_VAL})
    assert res.startswith("No matches found"), res


def test_r8_2_resolved_path_compare_via_symlink(test_workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from actions.codebase import _is_dynamic_secret
    repo = test_workspace["py"]
    real = repo / "real-auth.json"
    real.write_text("{}\n", encoding="utf-8")
    link = repo / "linkdir"
    link.symlink_to(repo)
    monkeypatch.setenv("REGISTRY_AUTH_FILE", str(link / "real-auth.json"))
    assert _is_dynamic_secret(real)          # different spelling, same resolved file
    assert not _is_dynamic_secret(repo / "main.py")


def test_r8_2_search_still_finds_normal_matches(test_workspace: dict[str, Path]) -> None:
    res = codebase({"action": "search", "root": str(test_workspace["py"]), "query": "Hello world"})
    assert "Hello world from python" in res and "main.py" in res


def test_r8_3_index_status_file_limit(test_workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    import actions.codebase as cb
    repo = test_workspace["py"]
    for i in range(30):
        (repo / f"f{i}.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(cb, "_WALK_MAX_FILES", 10)
    for action in ("status", "index"):
        res = codebase({"action": action, "root": str(repo)})
        assert "Truncated" in res and "10 files" in res, (action, res)


def test_r8_3_index_status_time_limit(test_workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    import actions.codebase as cb
    repo = test_workspace["py"]
    for i in range(5):
        (repo / f"d{i}").mkdir()
        (repo / f"d{i}" / "a.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(cb, "_WALK_MAX_SECONDS", 0.0)
    t0 = time.time()
    total, _, notice = cb._count_repo_files(repo)
    assert "Truncated" in notice and time.time() - t0 < 2
    res = codebase({"action": "status", "root": str(repo)})
    assert "Truncated" in res


def test_r8_3_small_repo_not_truncated(test_workspace: dict[str, Path]) -> None:
    res = codebase({"action": "status", "root": str(test_workspace["py"])})
    assert "Truncated" not in res


# ---------------------------------------------------------------------------
# Round 9 regression tests: (3) streaming walks with early stop, template search
# ---------------------------------------------------------------------------

def _scandir_spy(monkeypatch: pytest.MonkeyPatch, cb):
    counts = {"yielded": 0}
    real = os.scandir

    class _It:
        def __init__(self, inner):
            self._inner = inner
        def __iter__(self):
            for e in self._inner:
                counts["yielded"] += 1
                yield e
        def __enter__(self):
            return self
        def __exit__(self, *a):
            self._inner.close()

    monkeypatch.setattr(cb.os, "scandir", lambda p: _It(real(p)))
    return counts


def test_r9_3_tree_stops_reading_huge_directory(test_workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    import actions.codebase as cb
    repo = test_workspace["py"]
    big = repo / "big"
    big.mkdir()
    for i in range(500):
        (big / f"f{i:04d}.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(cb, "_TREE_MAX_DIR_ENTRIES", 50)
    counts = _scandir_spy(monkeypatch, cb)
    res = codebase({"action": "tree", "root": str(repo), "path": "big", "depth": 1})
    assert "listing stopped at 50 entries" in res, res[-300:]
    assert counts["yielded"] <= 60, counts


def test_r9_3_tree_total_entry_and_time_limits(test_workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    import actions.codebase as cb
    repo = test_workspace["py"]
    for i in range(10):
        d = repo / f"d{i}"
        d.mkdir()
        for j in range(10):
            (d / f"x{j}.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(cb, "_TREE_MAX_TOTAL_ENTRIES", 20)
    res = codebase({"action": "tree", "root": str(repo), "depth": 3})
    assert "Truncated: tree stopped after 20 entries" in res, res[-300:]
    monkeypatch.setattr(cb, "_TREE_MAX_TOTAL_ENTRIES", 20_000)
    monkeypatch.setattr(cb, "_TREE_MAX_SECONDS", 0.0)
    res = codebase({"action": "tree", "root": str(repo), "depth": 3})
    assert "Truncated: tree stopped after" in res


def test_r9_3_status_index_stop_inside_huge_directory(test_workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    import actions.codebase as cb
    repo = test_workspace["py"]
    big = repo / "big"
    big.mkdir()
    for i in range(500):
        (big / f"f{i:04d}.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(cb, "_WALK_MAX_DIR_ENTRIES", 40)
    counts = _scandir_spy(monkeypatch, cb)
    total, _, notice = cb._count_repo_files(big)
    assert "more than 40 entries" in notice and total <= 40
    assert counts["yielded"] <= 41, counts
    monkeypatch.setattr(cb, "_WALK_MAX_DIR_ENTRIES", 10_000)
    monkeypatch.setattr(cb, "_WALK_MAX_FILES", 25)
    counts["yielded"] = 0
    total, _, notice = cb._count_repo_files(big)
    assert total == 25 and "25 files" in notice and counts["yielded"] <= 26
    for action in ("status", "index"):
        res = codebase({"action": action, "root": str(repo)})
        assert "Truncated" in res, (action, res)


def test_r9_3_walk_no_os_walk_or_listdir(monkeypatch: pytest.MonkeyPatch, test_workspace: dict[str, Path]) -> None:
    import actions.codebase as cb
    def boom(*a, **k):
        raise AssertionError("full listing used")
    monkeypatch.setattr(cb.os, "walk", boom)
    monkeypatch.setattr(cb.os, "listdir", boom)
    repo = test_workspace["py"]
    assert "Files:" in codebase({"action": "status", "root": str(repo)})
    assert "main.py" in codebase({"action": "tree", "root": str(repo)})


def test_r9_opt_env_templates_searchable_secrets_not(secret_repo: Path) -> None:
    (secret_repo / ".env.example").write_text("TEMPLATE_ONLY_MARKER=\n", encoding="utf-8")
    (secret_repo / ".env.sample").write_text("TEMPLATE_ONLY_MARKER=\n", encoding="utf-8")
    (secret_repo / ".env").write_text(f"TEMPLATE_ONLY_MARKER={FAKE_DS}\n", encoding="utf-8")
    res = codebase({"action": "search", "root": str(secret_repo), "query": "TEMPLATE_ONLY_MARKER"})
    assert ".env.example" in res and ".env.sample" in res, res
    assert FAKE_DS not in res
    assert "/.env:" not in res and "\n.env:" not in res


def test_r9_opt_template_symlink_to_env_not_searched(test_workspace: dict[str, Path]) -> None:
    repo = test_workspace["py"]
    val = "symlinked" + "Template" + "Secret9"
    (repo / ".env").write_text(f"X={val}\n", encoding="utf-8")
    (repo / ".env.template").symlink_to(repo / ".env")
    res = codebase({"action": "search", "root": str(repo), "query": val})
    body = res.split("\n", 1)[1] if "\n" in res else ""
    assert val not in body, res
