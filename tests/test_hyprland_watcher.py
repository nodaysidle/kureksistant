"""Hyprland watcher parses socket2 events without spawning hyprctl on focus."""
from __future__ import annotations

from unittest.mock import MagicMock

import core.hyprland_watcher as hw


def test_focus_events_update_cache_without_subprocess(monkeypatch) -> None:
    watcher = hw.HyprlandEventWatcher()
    fake_run = MagicMock()
    monkeypatch.setattr(hw.subprocess, "run", fake_run)
    monkeypatch.setattr(hw.shutil, "which", lambda _name: "/usr/bin/hyprctl")
    watcher._instance_sig = "fake-instance"

    watcher.apply_event_line("activewindow>>kitty,~/dev/kureksistant")
    watcher.apply_event_line("activewindowv2>>0xabc123")
    watcher.apply_event_line("workspace>>3")
    watcher.apply_event_line("openwindow>>0xabc123,3,kitty,~/dev/kureksistant")
    watcher.apply_event_line("movewindow>>0xabc123,4")

    ctx = watcher.get_active_window_context()
    assert ctx["class"] == "kitty"
    assert ctx["title"] == "~/dev/kureksistant"
    assert ctx["address"] == "0xabc123"
    assert ctx["workspace"]["name"] == "4"
    assert watcher._geometry_dirty is True
    fake_run.assert_not_called()
    assert watcher.geometry_fetch_count == 0


def test_lazy_geometry_fetch_once_per_request(monkeypatch) -> None:
    watcher = hw.HyprlandEventWatcher()
    monkeypatch.setattr(hw.shutil, "which", lambda _name: "/usr/bin/hyprctl")
    watcher._instance_sig = "fake-instance"

    payload = {
        "class": "firefox",
        "title": "Docs",
        "address": "0xdead",
        "at": [10, 20],
        "size": [800, 600],
        "workspace": {"id": 2, "name": "2"},
    }
    calls = {"n": 0}

    def fake_activewindow():
        calls["n"] += 1
        return dict(payload)

    monkeypatch.setattr(watcher, "_hyprctl_activewindow", fake_activewindow)

    watcher.apply_event_line("activewindow>>firefox,Docs")
    watcher.apply_event_line("activewindowv2>>0xdead")
    assert calls["n"] == 0

    geom1 = watcher.get_active_window_geometry()
    geom2 = watcher.get_active_window_geometry()
    assert geom1 == (10, 20, 800, 600)
    assert geom2 == (10, 20, 800, 600)
    assert calls["n"] == 1
    assert watcher.geometry_fetch_count == 1

    # New focus dirties geometry → one more fetch on next request.
    watcher.apply_event_line("activewindow>>kitty,term")
    assert watcher._geometry_dirty is True
    geom3 = watcher.get_active_window_geometry()
    assert geom3 == (10, 20, 800, 600)
    assert calls["n"] == 2
    assert watcher.geometry_fetch_count == 2
