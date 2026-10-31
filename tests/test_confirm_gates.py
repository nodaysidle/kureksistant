"""The model cannot delete, write a secret or run an arbitrary command on its own say-so.

Every irreversible or sensitive action parks behind core/confirm.py, whose
token only the user's yes/no can resolve. rules.toml is refused outright.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from actions import _safe_paths, code_helper, dev_agent
from actions import file_controller as fc
from core import confirm


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    (h / "Downloads").mkdir(parents=True)
    (h / "Desktop").mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(fc, "_SAFE_ROOTS", [h])
    monkeypatch.setattr(fc, "_safe_trash", lambda t: (t.unlink(), f"Moved to Trash: {t.name}")[1])
    return h


@pytest.fixture
def hud(monkeypatch: pytest.MonkeyPatch):
    shown: list[tuple[str, str]] = []
    confirm.bind(lambda t, d: shown.append((t, d)), lambda: None)
    monkeypatch.setattr(confirm, "_pending", None)
    yield shown
    confirm.bind(None, None)
    confirm._pending = None


def _wait_for(cond, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# -- file_controller delete -------------------------------------------------

def test_delete_schema_has_no_model_confirm_flag() -> None:
    assert "confirmed" not in fc.TOOL["parameters"]["properties"]


def test_model_confirmed_flag_does_not_delete(home: Path, hud) -> None:
    f = home / "Downloads" / "old.apk"
    f.write_text("x")
    res = fc.file_controller({"action": "delete", "path": str(f), "confirmed": True})
    assert "[CONFIRMATION_PENDING]" in res
    assert f.exists()
    assert confirm.is_pending()
    assert hud and "old.apk" in hud[0][0]


def test_user_yes_deletes(home: Path, hud) -> None:
    f = home / "Downloads" / "old.apk"
    f.write_text("x")
    fc.delete_file(path=str(f))
    confirm.resolve(True)
    assert _wait_for(lambda: not f.exists())


def test_user_no_keeps_file(home: Path, hud) -> None:
    f = home / "Downloads" / "keep.txt"
    f.write_text("x")
    fc.delete_file(path=str(f))
    confirm.resolve(False)
    time.sleep(0.1)
    assert f.exists()


def test_delete_without_interface_does_nothing(home: Path, monkeypatch) -> None:
    confirm.bind(None, None)
    monkeypatch.setattr(confirm, "_pending", None)
    f = home / "Downloads" / "x.txt"
    f.write_text("x")
    res = fc.delete_file(path=str(f))
    assert "not done" in res.lower() or "not available" in res.lower()
    assert f.exists()


# -- file_controller writes -------------------------------------------------

def test_write_to_ssh_waits_for_confirmation(home: Path, hud) -> None:
    target = home / ".ssh" / "config"
    res = fc.write_file(path=str(target), content="Host evil\n")
    assert "[CONFIRMATION_PENDING]" in res
    assert not target.exists()
    confirm.resolve(True)
    assert _wait_for(target.exists)


def test_write_to_rules_toml_is_refused(home: Path, hud) -> None:
    rules = home / ".config" / "kurek" / "rules.toml"
    rules.parent.mkdir(parents=True)
    rules.write_text("original")
    res = fc.write_file(path=str(rules), content="hijacked")
    assert res.startswith("Refused")
    assert not confirm.is_pending()
    assert rules.read_text() == "original"


def test_create_file_on_rules_toml_is_refused(home: Path, hud) -> None:
    folder = home / ".config" / "kurek"
    folder.mkdir(parents=True)
    res = fc.create_file(path=str(folder), name="rules.toml", content="x")
    assert res.startswith("Refused")
    assert not (folder / "rules.toml").exists()


def test_ordinary_write_happens_at_once(home: Path, hud) -> None:
    target = home / "Downloads" / "note.md"
    res = fc.write_file(path=str(target), content="hello")
    assert "[CONFIRMATION_PENDING]" not in res
    assert target.read_text() == "hello"
    assert not confirm.is_pending()


def test_move_onto_sensitive_target_waits(home: Path, hud) -> None:
    src = home / "Downloads" / "rc"
    src.write_text("alias x=y")
    res = fc.move_file(path=str(src), destination=str(home / ".local" / "bin"))
    assert "[CONFIRMATION_PENDING]" in res
    assert src.exists()


# -- code_helper ------------------------------------------------------------

@pytest.mark.parametrize("raw", ["/etc/kurek_test.py", "~/.ssh/x.py", "~/.config/kurek/rules.toml"])
def test_code_helper_refuses_unsafe_output_paths(home: Path, raw: str) -> None:
    path = code_helper._resolve_save_path(raw, "python")
    assert code_helper._save_path_refusal(path)
    res = code_helper._save_file(path, "print(1)")
    assert "Refused" in res
    assert not path.exists()


def test_code_helper_saves_inside_home(home: Path) -> None:
    path = code_helper._resolve_save_path(str(home / "Desktop" / "a.py"), "python")
    assert code_helper._save_path_refusal(path) is None


# -- dev_agent --------------------------------------------------------------

def test_dev_agent_destructive_command_needs_a_human(tmp_path: Path, hud, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(dev_agent.subprocess, "run", lambda *a, **k: calls.append(a))
    res = dev_agent._run_project("rm -rf /tmp/kurek_should_not_exist", tmp_path)
    assert "[CONFIRMATION_PENDING]" in res
    assert calls == []


def test_dev_agent_allowlisted_command_runs(tmp_path: Path, hud, monkeypatch) -> None:
    calls = []

    def fake_run(*a, **k):
        calls.append(a)
        return subprocess.CompletedProcess(a[0] if a else [], 0, "Python 3", "")

    monkeypatch.setattr(dev_agent.subprocess, "run", fake_run)
    dev_agent._run_project("python --version", tmp_path)
    assert calls
    assert not confirm.is_pending()


def test_dev_agent_never_auto_installs(tmp_path: Path, hud, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(dev_agent.subprocess, "run", lambda *a, **k: calls.append(a))
    res = dev_agent._try_auto_install("ModuleNotFoundError: No module named 'evilpkg'", tmp_path)
    assert calls == []
    assert "[CONFIRMATION_PENDING]" in res


# -- _safe_paths ------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    ".ssh/id_ed25519", ".gnupg/pubring.kbx", ".password-store/x.gpg",
    ".local/share/keyrings/login.keyring", ".config/systemd/user/x.service",
    ".local/bin/kurek", ".config/kurek/anything", ".bashrc", ".zshrc", ".profile",
    ".netrc", "proj/.env", "certs/server.pem", "certs/server.key", "keys/id_rsa.pub",
    ".config/fish/config.fish",
])
def test_sensitive_paths(home: Path, rel: str) -> None:
    assert _safe_paths.is_sensitive(home / rel)


@pytest.mark.parametrize("rel", ["Downloads/KUREK/NOTES/n.md", "Desktop/a.py", "dev/proj/main.rs"])
def test_ordinary_paths(home: Path, rel: str) -> None:
    assert not _safe_paths.is_sensitive(home / rel)
    assert _safe_paths.is_within_home(home / rel)


def test_rules_file_and_outside_home(home: Path) -> None:
    assert _safe_paths.is_rules_file(home / ".config" / "kurek" / "rules.toml")
    assert not _safe_paths.is_within_home(Path("/etc/passwd"))


def test_dev_agent_second_request_is_reported_as_pending(tmp_path: Path, hud, monkeypatch) -> None:
    monkeypatch.setattr(dev_agent.subprocess, "run", lambda *a, **k: pytest.fail("ran"))
    dev_agent._run_project("rm -rf /tmp/kurek_x", tmp_path)
    second = dev_agent._try_auto_install("No module named 'evilpkg'", tmp_path)
    assert dev_agent._is_pending_confirmation(second)


def test_moving_a_key_out_of_ssh_waits(home: Path, hud) -> None:
    key = home / ".ssh" / "id_ed25519"
    key.parent.mkdir()
    key.write_text("secret")
    res = fc.move_file(path=str(key), destination=str(home / "Downloads"))
    assert "[CONFIRMATION_PENDING]" in res
    assert key.exists()


def test_rules_toml_cannot_be_renamed_or_deleted(home: Path, hud) -> None:
    rules = home / ".config" / "kurek" / "rules.toml"
    rules.parent.mkdir(parents=True)
    rules.write_text("original")
    assert fc.rename_file(path=str(rules), new_name="old.toml").startswith("Refused")
    assert fc.delete_file(path=str(rules)).startswith("Refused")
    assert rules.read_text() == "original"
    assert not confirm.is_pending()
