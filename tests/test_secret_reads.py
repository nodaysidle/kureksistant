"""file_controller read and file_processor refuse credential files, and only those."""
import pytest

import actions.file_controller as fc
from actions import file_processor as fp
from tests.test_file_paths import home  # noqa: F401  (fixture)

VALUE = "made-up-" + "Qx9" * 8


def _put(path, text=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"KEY={VALUE}\n" if text is None else text, encoding="utf-8")
    return path


@pytest.mark.parametrize("rel", [
    ".env", "proj/.env.local", "proj/.ENV", "proj/config/api_keys.json",
    ".ssh/id_ed25519", ".ssh/config", ".ssh/github_deploy", "keys/server.PEM",
    ".netrc", "proj/.git/config",
])
def test_read_refuses_secret_files(home, rel):  # noqa: F811
    _put(home / rel)
    out = fc.read_file(str(home / rel))
    assert out.startswith("Refused: ")
    assert VALUE not in out
    assert fc.file_controller({"action": "read", "path": str(home / rel)}).startswith("Refused: ")


def test_read_refuses_through_symlinks(home):  # noqa: F811
    _put(home / ".ssh" / "id_ed25519")
    _put(home / ".ssh" / "config")
    (home / "Desktop" / "notes.txt").symlink_to(home / ".ssh" / "id_ed25519")
    (home / "Desktop" / "linkdir").symlink_to(home / ".ssh")
    for path in ("desktop/notes.txt", "desktop/linkdir/config"):
        out = fc.read_file(path)
        assert out.startswith("Refused: "), path
        assert VALUE not in out


def test_read_refuses_bare_names_and_missing_secrets(home):  # noqa: F811
    _put(home / "Downloads" / "KUREK" / "NOTES" / ".env")
    _put(home / "Desktop" / ".env")
    assert fc.read_file(".env").startswith("Refused: ")
    assert fc.read_file("desktop", name=".env").startswith("Refused: ")
    assert fc.read_file(str(home / "nope" / ".env")).startswith("Refused: ")


def test_relative_escape_is_still_access_denied(home):  # noqa: F811
    assert fc.read_file("../" * 10 + "etc/.env").startswith("Access denied")


def test_ordinary_files_stay_readable(home):  # noqa: F811
    _put(home / "Desktop" / "a.md", "hello")
    _put(home / "proj" / ".env.example", "X=1")
    assert fc.read_file(str(home / "Desktop" / "a.md")) == "hello"
    assert fc.read_file(str(home / "proj" / ".env.example")) == "X=1"
    assert fc.secret_file_refusal(home / "Desktop" / "a.md") is None


@pytest.mark.parametrize("rel", [
    ".bashrc", ".zshrc", ".profile", ".config/kurek/rules.toml",
    ".config/systemd/user/kurek.service", ".local/bin/backup-notes",
])
def test_config_files_that_are_not_secrets_stay_readable(home, rel):  # noqa: F811
    text = f"# {rel}\nplain settings\n"
    _put(home / rel, text)
    assert fc.file_controller({"action": "read", "path": str(home / rel)}) == text


@pytest.mark.parametrize("rel", [".env", "proj/config/api_keys.json", ".ssh/id_rsa", "proj/.envrc"])
def test_file_processor_refuses_secret_files(home, rel, monkeypatch):  # noqa: F811
    monkeypatch.setattr(fp, "_gemini_client", lambda: pytest.fail("a secret reached Gemini"))
    _put(home / rel)
    out = fp.file_processor({"file_path": str(home / rel)})
    assert out.startswith("Refused: ")
    assert VALUE not in out


def test_file_processor_refuses_relative_and_linked_secrets(home, monkeypatch):  # noqa: F811
    monkeypatch.setattr(fp, "_gemini_client", lambda: pytest.fail("a secret reached Gemini"))
    _put(home / "proj" / ".env")
    monkeypatch.chdir(home / "proj")
    assert fp.file_processor({"file_path": ".env"}).startswith("Refused: ")
    _put(home / ".ssh" / "id_rsa")
    (home / "Desktop" / "data.txt").symlink_to(home / ".ssh" / "id_rsa")
    out = fp.file_processor({"file_path": str(home / "Desktop" / "data.txt")})
    assert out.startswith("Refused: ")
    assert VALUE not in out


def test_file_processor_still_sends_ordinary_unknown_files(home, monkeypatch):  # noqa: F811
    from types import SimpleNamespace
    sent = []
    model = SimpleNamespace(generate_content=lambda p: sent.append(p) or SimpleNamespace(text=" described "))
    monkeypatch.setattr(fp, "_gemini_client", lambda: model)
    _put(home / "notes.xyz", "shopping list")
    assert fp.file_processor({"file_path": str(home / "notes.xyz")}) == "described"
    assert "shopping list" in sent[0]


def test_file_processor_still_reads_ordinary_files(home):  # noqa: F811
    pytest.importorskip("pandas")
    _put(home / "a.csv", "x,y\n1,2\n")
    assert "Rows: 1, Columns: 2" in fp.file_processor({"file_path": str(home / "a.csv"), "action": "info"})


@pytest.mark.parametrize("proc", ["/proc/self/environ", "/proc/1/environ"])
def test_file_processor_refuses_process_environments(proc, monkeypatch):
    monkeypatch.setattr(fp, "_gemini_client", lambda: pytest.fail("a process environment reached Gemini"))
    assert fp.file_processor({"file_path": proc}).startswith("Refused: ")


def test_file_processor_refuses_a_link_to_a_process_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "_gemini_client", lambda: pytest.fail("a process environment reached Gemini"))
    (tmp_path / "data.txt").symlink_to("/proc/self/environ")
    assert fp.file_processor({"file_path": str(tmp_path / "data.txt")}).startswith("Refused: ")


@pytest.fixture
def parked(monkeypatch):
    """Confirmation requests are recorded and parked, never shown."""
    requests = []
    monkeypatch.setattr(fc.confirm, "pending_title", lambda: "")
    monkeypatch.setattr(fc.confirm, "request", lambda **k: requests.append(k) or "parked")
    return requests


def test_a_secret_cannot_be_copied_to_an_innocent_name(home, parked):  # noqa: F811
    _put(home / "proj" / ".env")
    out = fc.file_controller({"action": "copy", "path": str(home / "proj" / ".env"),
                              "destination": str(home / "Desktop" / "env.txt")})
    assert out.startswith("Refused: ")
    assert not (home / "Desktop" / "env.txt").exists()
    _put(home / ".ssh" / "deploy_key")
    assert fc.copy_file(str(home / ".ssh" / "deploy_key"), destination="desktop/notes.txt").startswith("Refused: ")
    assert fc.move_file(str(home / ".ssh" / "deploy_key"), destination="desktop/notes.txt").startswith("Refused: ")
    assert not (home / "Desktop" / "notes.txt").exists()
    assert (home / ".ssh" / "deploy_key").exists()
    assert parked == []


def test_a_secret_cannot_be_renamed_to_an_innocent_name(home, parked):  # noqa: F811
    _put(home / "proj" / ".env")
    assert fc.rename_file(str(home / "proj" / ".env"), new_name="notes.txt").startswith("Refused: ")
    assert (home / "proj" / ".env").exists() and not (home / "proj" / "notes.txt").exists()
    assert parked == []


def test_a_secret_kept_under_a_secret_name_behaves_as_before(home, parked):  # noqa: F811
    _put(home / "proj" / ".env")
    assert fc.copy_file(str(home / "proj" / ".env"), destination=str(home / "proj" / ".env.bak")).startswith("Copied: ")
    assert (home / "proj" / ".env.bak").read_text(encoding="utf-8") == f"KEY={VALUE}\n"
    assert fc.move_file(str(home / "proj" / ".env"), destination=str(home / "proj" / ".env.old")) == "parked"
    _put(home / ".ssh" / "id_ed25519")
    assert fc.rename_file(str(home / ".ssh" / "id_ed25519"), new_name="old_key") == "parked"
    assert len(parked) == 2


def test_ordinary_copy_move_and_rename_behave_as_before(home, parked):  # noqa: F811
    _put(home / "Desktop" / "a.md", "hello")
    assert fc.copy_file("desktop/a.md", destination="desktop/b.md").startswith("Copied: ")
    assert fc.move_file("desktop/b.md", destination=str(home / "Downloads")).startswith("Moved: ")
    assert fc.rename_file(str(home / "Downloads" / "b.md"), new_name="c.md").startswith("Renamed: ")
    assert (home / "Downloads" / "c.md").read_text(encoding="utf-8") == "hello"
    assert parked == []
