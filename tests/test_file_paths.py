"""file_controller says where a file went and whether it replaced one."""
import pytest

import actions.file_controller as fc


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "Downloads").mkdir(parents=True)
    (h / "Desktop").mkdir()
    monkeypatch.setenv("HOME", str(h))
    for var in ("XDG_DOWNLOAD_DIR", "XDG_DESKTOP_DIR", "XDG_DOCUMENTS_DIR"):
        monkeypatch.delenv(var, raising=False)  # never resolve a shortcut to the real home
    monkeypatch.setattr(fc, "_SAFE_ROOTS", [h])
    monkeypatch.setattr(fc, "push_undo", lambda *a, **k: None)
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")  # old code writes cwd-relative
    return h


def test_create_over_an_existing_file_says_it_replaced_it(home):
    p = home / "Desktop" / "a.md"
    fc.create_file(path=str(p), content="héllo")
    # 5 chars, 6 bytes: the count is in characters.
    assert fc.create_file(path=str(p), content="bye") == f"Replaced existing file: {p} (it had 5 chars)"
    assert p.read_text(encoding="utf-8") == "bye"


def test_create_of_a_new_file_gives_the_absolute_path(home):
    p = home / "Desktop" / "new.md"
    assert fc.create_file(path=str(p), content="x") == f"File created: {p}"


def test_write_reports_replaced_and_new_files(home):
    p = home / "Desktop" / "w.md"
    p.write_text("hello", encoding="utf-8")
    assert fc.write_file(path=str(p), content="x") == f"Replaced existing file: {p} (it had 5 chars)"
    q = home / "Desktop" / "fresh.md"
    assert fc.write_file(path=str(q), content="x") == f"Written to: {q}"


def test_append_through_the_tool(home):
    p = home / "Desktop" / "log.md"
    p.write_text("a", encoding="utf-8")
    out = fc.file_controller({"action": "write", "path": str(p), "content": "b", "append": True})
    assert out == f"Appended to: {p}"
    assert p.read_text(encoding="utf-8") == "ab"


def test_append_is_in_the_schema():
    assert fc.TOOL["parameters"]["properties"]["append"]["type"] == "BOOLEAN"


def _notes(home):
    return home / "Downloads" / "KUREK" / "NOTES"


def test_a_bare_name_goes_to_the_notes_folder_not_the_cwd(home):
    notes = _notes(home)
    assert fc.create_file(path="kurek.md", content="hi") == f"File created: {notes / 'kurek.md'}"
    assert (notes / "kurek.md").read_text(encoding="utf-8") == "hi"
    assert list((home.parent / "cwd").iterdir()) == []
    assert fc.read_file("kurek.md") == "hi"
    assert "kurek.md" in fc.list_files("")


def test_a_relative_folder_goes_under_the_notes_folder(home):
    fc.file_controller({"action": "write", "path": "ideas", "name": "x.md", "content": "c"})
    assert (_notes(home) / "ideas" / "x.md").read_text(encoding="utf-8") == "c"


def test_kurek_notes_dir_overrides_the_notes_folder(home, monkeypatch):
    monkeypatch.setenv("KUREK_NOTES_DIR", "~/MyNotes")
    fc.create_file(path="a.md", content="n")
    assert (home / "MyNotes" / "a.md").read_text(encoding="utf-8") == "n"


def test_shortcuts_home_and_absolute_paths_are_unchanged(home):
    fc.create_file(path="downloads/d.md", content="d")
    fc.create_file(path="~/t.md", content="t")
    fc.create_file(path=str(home / "Desktop" / "b.md"), content="b")
    assert (home / "Downloads" / "d.md").read_text(encoding="utf-8") == "d"
    assert (home / "t.md").read_text(encoding="utf-8") == "t"
    assert (home / "Desktop" / "b.md").read_text(encoding="utf-8") == "b"


def test_relative_escapes_are_refused(home):
    up = "../" * 10
    assert fc.read_file(f"{up}etc/hostname").startswith("Access denied")
    assert fc.create_file(path=f"{up}tmp/x.md").startswith("Access denied")


def test_delete_of_a_bare_name_never_falls_back_to_the_cwd(home, monkeypatch):
    repo = home / "repo"  # the daemon's cwd is the repo, inside home
    repo.mkdir()
    (repo / "AGENTS.md").write_text("keep me", encoding="utf-8")
    monkeypatch.chdir(repo)
    requested: list = []
    monkeypatch.setattr(fc.confirm, "pending_title", lambda: "")
    monkeypatch.setattr(fc.confirm, "request", lambda **k: requested.append(k) or "parked")
    assert fc.delete_file("AGENTS.md") == "File not found: AGENTS.md"
    assert requested == []
    assert (repo / "AGENTS.md").exists()


def test_a_relative_kurek_notes_dir_is_under_home(home, monkeypatch):
    monkeypatch.setenv("KUREK_NOTES_DIR", "notes")
    fc.create_file(path="a.md", content="r")
    assert (home / "notes" / "a.md").read_text(encoding="utf-8") == "r"
