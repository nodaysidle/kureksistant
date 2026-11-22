"""The clipboard watcher runs only in the daemon."""
import threading
from types import SimpleNamespace

import actions.clipboard_tool as ct
import kurek_daemon as daemon
from tests.test_stream_errors import _make_lite_engine


def test_importing_the_tool_starts_no_watcher():
    assert not any(t.name == "ClipboardWatcher" for t in threading.enumerate())
    assert ct._WATCHER_RUNNING is False


def test_the_daemon_starts_the_watcher_only_with_the_clipboard_tool(monkeypatch):
    started = []
    monkeypatch.setattr(ct, "start_clipboard_watcher", lambda: started.append(1))
    _make_lite_engine(monkeypatch)
    daemon.KurekEngine()
    assert started == []
    monkeypatch.setattr(daemon, "discover_actions", lambda *a, **k: SimpleNamespace(
        names=lambda: {"manage_clipboard"}, get_tool_declarations=lambda: [], run=lambda *a, **k: "ok"))
    daemon.KurekEngine()
    assert started == [1]


HINTED = "text/plain;charset=utf-8\nx-kde-passwordManagerHint\ntext/plain\n"
PLAIN = "text/plain\nUTF8_STRING\n"


def _fake_wl(monkeypatch, types, clip: str, list_rc: int = 0) -> list:
    """wl-paste stand-in, installed on the clipboard module only (never on the real subprocess).
    types is one listing, or a list of listings returned in turn (the last one repeats)."""
    calls = []
    listings = [types] if isinstance(types, str) else list(types)

    def run(argv, **kw):
        calls.append((argv, kw))
        if argv[1:] == ["--list-types"]:
            return SimpleNamespace(returncode=list_rc, stdout=listings.pop(0) if len(listings) > 1 else listings[0])
        return SimpleNamespace(returncode=0, stdout=clip)

    monkeypatch.setattr(ct, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(ct, "shutil", SimpleNamespace(
        which=lambda n: "/usr/bin/wl-paste" if n == "wl-paste" else None))
    monkeypatch.setattr(ct, "subprocess", SimpleNamespace(run=run))
    return calls


def test_a_clip_marked_by_a_password_manager_is_never_read(monkeypatch):
    calls = _fake_wl(monkeypatch, HINTED, "hunter-two")
    assert ct._read_system_clipboard() == ""
    assert [c[0] for c in calls] == [["wl-paste", "--list-types"]]


def test_an_ordinary_clip_is_read_with_bounded_calls(monkeypatch):
    calls = _fake_wl(monkeypatch, PLAIN, "hello")
    assert ct._read_system_clipboard() == "hello"
    assert [c[0][1] for c in calls] == ["--list-types", "--no-newline", "--list-types"]
    assert all("timeout" in kw and kw["timeout"] <= 2 for _argv, kw in calls)


def test_the_watcher_stores_nothing_a_password_manager_marked(monkeypatch, tmp_path):
    store = ct.ClipboardStore(tmp_path / "h.json")
    monkeypatch.setattr(ct, "_GLOBAL_STORE", store)
    monkeypatch.setattr(ct, "_LAST_SEEN_CLIP", "")
    monkeypatch.setattr(ct, "_WATCHER_RUNNING", False)  # the loop then only seeds once
    _fake_wl(monkeypatch, HINTED, "hunter-two")
    ct._clipboard_watcher_loop()
    assert store.items == []
    assert not (tmp_path / "h.json").exists()
    _fake_wl(monkeypatch, PLAIN, "plain text")
    ct._clipboard_watcher_loop()
    assert store.items[0]["text"] == "plain text"


def _watcher_store(monkeypatch, tmp_path):
    store = ct.ClipboardStore(tmp_path / "h.json")
    monkeypatch.setattr(ct, "_GLOBAL_STORE", store)
    monkeypatch.setattr(ct, "_LAST_SEEN_CLIP", "")
    monkeypatch.setattr(ct, "_WATCHER_RUNNING", False)  # the loop then only seeds once
    return store


def test_a_failed_type_listing_reads_and_stores_nothing(monkeypatch, tmp_path):
    store = _watcher_store(monkeypatch, tmp_path)
    calls = _fake_wl(monkeypatch, PLAIN, "hunter-two", list_rc=1)
    assert ct._read_system_clipboard() == ""
    assert [c[0] for c in calls] == [["wl-paste", "--list-types"]]
    ct._clipboard_watcher_loop()
    assert store.items == []
    assert not (tmp_path / "h.json").exists()


def test_a_clip_marked_between_listing_and_paste_is_not_stored(monkeypatch, tmp_path):
    store = _watcher_store(monkeypatch, tmp_path)
    _fake_wl(monkeypatch, [PLAIN, HINTED], "hunter-two")
    ct._clipboard_watcher_loop()
    assert store.items == []
    assert not (tmp_path / "h.json").exists()
