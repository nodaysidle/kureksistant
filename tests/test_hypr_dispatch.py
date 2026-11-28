"""hypr_dispatch: Lua form first, legacy fallback, exit code 0 is not proof of success."""
from __future__ import annotations

import json
import subprocess

import pytest

from actions import _hypr, computer_control, computer_settings, open_app

LUA_ERR = "error: 3 [string \"return hl.dispatch(focuswindow address:0x0)\"]:1: ')' expected"


def _fake(monkeypatch, replies):
    """replies: list of (stdout, returncode) consumed in order; returns the recorded argv list."""
    calls, queue = [], list(replies)

    def run(cmd, *a, **k):
        calls.append(list(cmd))
        out, rc = queue.pop(0) if queue else ("ok", 0)
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_clean_lua_reply_is_accepted(monkeypatch):
    calls = _fake(monkeypatch, [("ok", 0)])
    assert _hypr.hypr_dispatch("hl.dsp.window.close()", ["killactive"]) is True
    assert calls == [["hyprctl", "dispatch", "hl.dsp.window.close()"]]


def test_error_reply_falls_back_to_legacy(monkeypatch):
    calls = _fake(monkeypatch, [(LUA_ERR, 0), ("ok", 0)])
    assert _hypr.hypr_dispatch("hl.dsp.window.close()", ["killactive"]) is True
    assert calls[1] == ["hyprctl", "dispatch", "killactive"]


def test_both_forms_failing_is_failure(monkeypatch):
    _fake(monkeypatch, [(LUA_ERR, 0), ("Invalid dispatcher", 0)])
    assert _hypr.hypr_dispatch("hl.dsp.window.close()", ["killactive"]) is False


def test_not_found_warning_is_not_done_and_not_retried(monkeypatch):
    calls = _fake(monkeypatch, [("warning: =[C]:-1: hl.focus: window not found", 0)])
    assert _hypr.hypr_dispatch('hl.dsp.focus({ window = "address:0x0" })',
                               ["focuswindow", "address:0x0"]) is False
    assert len(calls) == 1


def test_exit_code_zero_with_error_line_is_never_success(monkeypatch):
    _fake(monkeypatch, [(LUA_ERR, 0), (LUA_ERR, 0)])
    assert _hypr.hypr_dispatch("hl.dsp.window.close()", ["killactive"]) is False


def test_missing_or_hung_hyprctl_is_failure(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("hyprctl")
    monkeypatch.setattr(subprocess, "run", boom)
    assert _hypr.hypr_dispatch("hl.dsp.window.close()", ["killactive"]) is False


# One test per call-site family: the caller must not claim success when Hyprland refuses.

@pytest.fixture
def refuse(monkeypatch):
    """hyprctl reads work, every dispatch is refused."""
    clients = json.dumps([{"class": "firefox", "title": "Firefox", "initialClass": "firefox",
                           "address": "0xabc", "workspace": {"id": 3}}])
    monitors = json.dumps([{"focused": True, "width": 1920, "height": 1080, "x": 0, "y": 0,
                            "scale": 1.0, "reserved": [0, 0, 0, 0]}])

    def run(cmd, *a, **k):
        out = LUA_ERR
        if cmd[:2] == ["hyprctl", "clients"]:
            out = clients
        elif cmd[:2] == ["hyprctl", "monitors"]:
            out = monitors
        elif cmd[:2] == ["hyprctl", "activewindow"]:
            out = "{}"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(open_app.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(computer_control.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(computer_control, "_get_os", lambda: "linux")
    monkeypatch.setattr(computer_control.time, "sleep", lambda s: None)
    monkeypatch.setattr(computer_settings, "_OS", "Linux")
    monkeypatch.setattr(computer_settings, "_is_hyprland", lambda: True)


def test_open_app_focus_reports_failure(refuse):
    assert open_app._focus_hyprland_app("firefox") is False


def test_computer_control_focus_does_not_claim_success(refuse):
    out = computer_control._focus_window("firefox")
    assert not out.startswith("Focused") and "did not accept" in out


@pytest.mark.parametrize("fn", ["close_app", "close_window", "full_screen", "maximize_window",
                                "snap_left", "snap_right", "switch_window", "sleep_display"])
def test_computer_settings_actions_raise_on_refusal(refuse, fn):
    with pytest.raises(RuntimeError, match="Hyprland did not accept"):
        getattr(computer_settings, fn)()
