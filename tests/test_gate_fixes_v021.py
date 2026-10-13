"""Regression tests for the PR #13 merge-gate findings."""
from __future__ import annotations

import threading
import time

import pytest

from actions import computer_control, computer_settings, open_app
from core import action_loader


class _Rec:
    def __init__(self, handler):
        self.handler = handler
        self.valid = True


def test_action_timeout_returns_promptly_without_waiting_for_stuck_thread():
    release = threading.Event()

    def stuck(parameters=None, **_):
        release.wait(10)
        return "late"

    reg = action_loader.ActionRegistry.__new__(action_loader.ActionRegistry)
    reg._actions = {"stuck": _Rec(stuck)}
    reg._logger = lambda *_: None
    t0 = time.monotonic()
    out = reg.run("stuck", {}, timeout=0.3)
    elapsed = time.monotonic() - t0
    release.set()
    assert "timed out" in out
    assert elapsed < 2.0


class _Run:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, *a, **k):
        self.calls.append(list(cmd))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()


def test_maximize_uses_fullscreen_mode_1_on_hyprland(monkeypatch):
    run = _Run()
    monkeypatch.setattr(computer_settings, "_OS", "Linux")
    monkeypatch.setattr(computer_settings, "_is_hyprland", lambda: True)
    monkeypatch.setattr(computer_settings.subprocess, "run", run)
    computer_settings.maximize_window()
    assert ["hyprctl", "dispatch", "fullscreen", "1"] in run.calls


def test_close_tab_uses_ctrl_w_and_close_window_uses_killactive(monkeypatch):
    run = _Run()
    keys = []
    monkeypatch.setattr(computer_settings, "_OS", "Linux")
    monkeypatch.setattr(computer_settings, "_is_hyprland", lambda: True)
    monkeypatch.setattr(computer_settings.subprocess, "run", run)
    monkeypatch.setattr(
        computer_settings.pyautogui, "hotkey", lambda *k: keys.append(k)
    )
    computer_settings.close_tab()
    assert keys == [("ctrl", "w")] and run.calls == []
    computer_settings.close_window()
    assert run.calls == [["hyprctl", "dispatch", "killactive"]]


@pytest.mark.parametrize("phrase,action", [
    ("close this", "close_tab"),
    ("close it", "close_tab"),
    ("close the tab", "close_tab"),
    ("close this window", "close_window"),
    ("close the window", "close_window"),
])
def test_close_phrases_route_correctly(phrase, action):
    assert computer_settings._detect_action(phrase)["action"] == action


def _assert_valid_hypr(calls):
    for c in calls:
        assert c[0] == "hyprctl"
        assert "eval" not in c
        assert not any("hl.dsp" in part for part in c)
    disp = [c for c in calls if c[:2] == ["hyprctl", "dispatch"]]
    assert ["hyprctl", "dispatch", "workspace", "3"] in disp
    assert ["hyprctl", "dispatch", "focuswindow", "address:0xabc"] in disp


def test_open_app_focus_uses_valid_dispatchers(monkeypatch):
    import json
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(list(cmd))

        class R:
            returncode = 0
            stderr = ""
            stdout = json.dumps([{"class": "firefox", "title": "Firefox",
                                  "initialClass": "firefox",
                                  "address": "0xabc", "workspace": {"id": 3}}]) \
                if cmd[:2] == ["hyprctl", "clients"] else ""
        return R()

    monkeypatch.setattr(open_app.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(open_app.subprocess, "run", fake_run)
    assert open_app._focus_hyprland_app("firefox") is True
    _assert_valid_hypr(calls)


def test_computer_control_source_has_no_invalid_hypr_syntax():
    import inspect
    src = inspect.getsource(computer_control._focus_window)
    assert "hl.dsp" not in src and '"eval"' not in src
    assert '"focuswindow"' in src
