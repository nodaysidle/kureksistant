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
    assert out.startswith("TIMED OUT") and "NOT retried" in out
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
    assert run.calls == [["hyprctl", "dispatch", 'hl.dsp.window.fullscreen({ mode = "maximized" })']]


def test_close_tab_uses_ctrl_w_and_close_window_uses_killactive(monkeypatch):
    run = _Run()
    keys = []
    # pyautogui path (no Wayland session); the wtype path is covered in
    # tests/test_actions_hardening.py.
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(computer_settings, "_OS", "Linux")
    monkeypatch.setattr(computer_settings, "_is_hyprland", lambda: True)
    monkeypatch.setattr(computer_settings.subprocess, "run", run)
    monkeypatch.setattr(
        computer_settings.pyautogui, "hotkey", lambda *k: keys.append(k)
    )
    computer_settings.close_tab()
    assert keys == [("ctrl", "w")] and run.calls == []
    computer_settings.close_window()
    assert run.calls == [["hyprctl", "dispatch", "hl.dsp.window.close()"]]


@pytest.mark.parametrize("phrase,action", [
    ("close this", "close_tab"),
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
    disp = [c for c in calls if c[:2] == ["hyprctl", "dispatch"]]
    assert ["hyprctl", "dispatch", "hl.dsp.focus({ workspace = 3 })"] in disp
    assert ["hyprctl", "dispatch", 'hl.dsp.focus({ window = "address:0xabc" })'] in disp


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
    assert '"eval"' not in src
    assert "hypr_dispatch(" in src and '"focuswindow"' in src


def test_timed_out_tool_is_not_success_and_not_retried():
    release = threading.Event()
    calls = []

    def stuck(parameters=None, **_):
        calls.append(1)
        release.wait(10)
        return "Done."

    reg = action_loader.ActionRegistry.__new__(action_loader.ActionRegistry)
    reg._actions = {"stuck": _Rec(stuck)}
    reg._logger = lambda *_: None
    first = reg.run("stuck", {}, timeout=0.2)
    assert first.startswith("TIMED OUT")
    assert "NOT retried" in first and "Done" not in first
    second = reg.run("stuck", {}, timeout=0.2)
    assert second.startswith("TIMED OUT") and "NOT run again" in second
    assert len(calls) == 1
    release.set()
    for _ in range(50):
        if not reg._timed_out_running["stuck"].done():
            time.sleep(0.02)
    assert reg.run("stuck", {}, timeout=2) == "Done."
    assert len(calls) == 2


@pytest.mark.parametrize("side,x", [("left", 0), ("right", 1270)])
def test_hyprland_snap_is_real_half_screen(monkeypatch, side, x):
    import json
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(list(cmd))

        class R:
            returncode = 0
            stderr = ""
            stdout = json.dumps([{"focused": True, "x": 0, "y": 0,
                                  "width": 2560, "height": 1440, "scale": 1.0,
                                  "reserved": [0, 30, 20, 0]}]) \
                if cmd[:2] == ["hyprctl", "monitors"] else ""
        return R()

    monkeypatch.setattr(computer_settings, "_OS", "Linux")
    monkeypatch.setattr(computer_settings, "_is_hyprland", lambda: True)
    monkeypatch.setattr(computer_settings.subprocess, "run", fake_run)
    (computer_settings.snap_left if side == "left" else computer_settings.snap_right)()
    disp = [c[2] for c in calls if c[:2] == ["hyprctl", "dispatch"]]
    assert not any("movewindow" in d for d in disp)
    assert disp == ['hl.dsp.window.float({ action = "set" })',
                    "hl.dsp.window.resize({ x = 1270, y = 1410 })",
                    f"hl.dsp.window.move({{ x = {x}, y = 30 }})"]


@pytest.mark.parametrize("state,target", [
    (0, "2"),                    # windowed -> fullscreen
    (1, "2"),                    # maximized -> fullscreen
    (2, "0"),                    # fullscreen -> off
    (True, "0"),                 # older Hyprland bool form
    (False, "2"),
])
def test_hyprland_fullscreen_toggles(monkeypatch, state, target):
    import json
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(list(cmd))

        class R:
            returncode = 0
            stderr = ""
            stdout = json.dumps({"fullscreen": state}) \
                if cmd[:2] == ["hyprctl", "activewindow"] else ""
        return R()

    monkeypatch.setattr(computer_settings, "_OS", "Linux")
    monkeypatch.setattr(computer_settings, "_is_hyprland", lambda: True)
    monkeypatch.setattr(computer_settings.subprocess, "run", fake_run)
    computer_settings.full_screen()
    assert ["hyprctl", "dispatch",
            f"hl.dsp.window.fullscreen_state({{ internal = {target}, client = -1 }})"] in calls
    assert not any(c[:3] == ["hyprctl", "dispatch", "fullscreen"] for c in calls)


_ASK_CLOSE = ('Not done: "close it" does not say what to close. '
              "Ask the user which window, tab or app they mean.")


@pytest.fixture
def no_keys(monkeypatch):
    """Record every hotkey and subprocess instead of sending it."""
    keys, run = [], _Run()
    monkeypatch.setattr(computer_settings, "_PYAUTOGUI", True)
    monkeypatch.setattr(computer_settings, "_OS", "Linux")
    monkeypatch.setattr(computer_settings, "_hk", lambda *k: keys.append(k))
    monkeypatch.setattr(computer_settings.subprocess, "run", run)
    return keys, run


@pytest.mark.parametrize("phrase", ["close it", "Close it.", " close it ", "close it!"])
def test_bare_close_it_asks_what_to_close_and_sends_nothing(no_keys, phrase):
    keys, run = no_keys
    assert computer_settings.computer_settings({"description": phrase}) == _ASK_CLOSE
    assert keys == [] and run.calls == []
    assert computer_settings._detect_action(phrase)["action"] not in ("close_tab", "close_window")


@pytest.mark.parametrize("phrase", ["close tab", "close this"])
def test_close_tab_phrases_still_resolve(phrase):
    assert computer_settings._detect_action(phrase)["action"] == "close_tab"


def test_explicit_close_tab_action_still_sends_ctrl_w(no_keys):
    keys, _run = no_keys
    assert computer_settings.computer_settings({"action": "close_tab"}) == "Done: close_tab."
    assert keys == [("ctrl", "w")]
