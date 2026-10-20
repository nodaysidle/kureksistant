"""confirm._log fallback/replacement logging and Hyprland watcher reconnect."""
from __future__ import annotations

import core.confirm as confirm
import core.hyprland_watcher as hw


def test_log_prints_without_callback(monkeypatch, capsys) -> None:
    monkeypatch.setattr(confirm, "_log_cb", None)
    confirm._log("ERR: thing failed")
    assert "ERR: thing failed" in capsys.readouterr().out


def test_log_uses_callback_when_bound(monkeypatch, capsys) -> None:
    got = []
    monkeypatch.setattr(confirm, "_log_cb", got.append)
    confirm._log("hello")
    assert got == ["hello"]
    assert "hello" not in capsys.readouterr().out


def test_request_logs_when_replacing_pending(monkeypatch, capsys) -> None:
    monkeypatch.setattr(confirm, "_show_cb", lambda *_a: None)
    monkeypatch.setattr(confirm, "_hide_cb", None)
    monkeypatch.setattr(confirm, "_log_cb", None)
    monkeypatch.setattr(confirm, "_pending", None)
    confirm.request("a", "Shutdown", "", lambda: "ok")
    capsys.readouterr()
    confirm.request("b", "Restart", "", lambda: "ok")
    out = capsys.readouterr().out
    assert "Replaced pending confirmation" in out and "Shutdown" in out
    assert confirm.pending_title() == "Restart"
    monkeypatch.setattr(confirm, "_pending", None)


def test_listen_loop_reresolves_socket_after_failure(monkeypatch, tmp_path) -> None:
    stale = tmp_path / "old.sock"
    stale.write_text("")
    fresh = tmp_path / "new.sock"
    fresh.write_text("")
    resolved = []

    watcher = hw.HyprlandEventWatcher()
    watcher._running = True
    watcher._sock_path = str(stale)

    def resolve():
        resolved.append(1)
        watcher._running = False  # stop after the re-resolve
        return str(fresh), "newsig"

    connects = []

    class BadSock:
        def connect(self, p):
            connects.append(p)
            raise ConnectionRefusedError("gone")

        def close(self):
            pass

    monkeypatch.setattr(hw, "_resolve_hyprland_socket2", resolve)
    monkeypatch.setattr(hw.socket, "socket", lambda *_a: BadSock())
    monkeypatch.setattr(hw.time, "sleep", lambda _s: None)
    watcher._listen_loop()
    assert resolved == [1]
    assert connects == [str(stale), str(fresh)]
    assert watcher._instance_sig == "newsig"


def test_get_hyprland_watcher_singleton(monkeypatch) -> None:
    starts = []
    monkeypatch.setattr(hw, "_GLOBAL_WATCHER", None)
    monkeypatch.setattr(hw.HyprlandEventWatcher, "start", lambda self: starts.append(self))
    a = hw.get_hyprland_watcher()
    b = hw.get_hyprland_watcher()
    assert a is b and len(starts) == 1
