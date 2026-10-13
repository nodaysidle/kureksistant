"""Unit tests verifying terminal and Omarchy/Hyprland fallbacks."""
from __future__ import annotations

import shutil
from unittest.mock import patch
import pytest

from actions import open_app, computer_settings


def test_linux_terminal_fallbacks_prioritize_ghostty_and_herdr():
    assert len(open_app._LINUX_TERMINAL_FALLBACKS) >= 2
    assert open_app._LINUX_TERMINAL_FALLBACKS[0] == "ghostty"
    assert open_app._LINUX_TERMINAL_FALLBACKS[1] == "herdr"


def test_app_aliases_include_ghostty_herdr_fish():
    assert "ghostty" in open_app._APP_ALIASES
    assert open_app._APP_ALIASES["ghostty"]["Linux"] == "ghostty"

    assert "herdr" in open_app._APP_ALIASES
    assert open_app._APP_ALIASES["herdr"]["Linux"] == "herdr"

    assert "fish" in open_app._APP_ALIASES
    assert open_app._APP_ALIASES["fish"]["Linux"] == "ghostty -e fish"


def test_computer_settings_hyprland_dispatch(monkeypatch):
    monkeypatch.setattr(computer_settings, "_OS", "Linux")
    monkeypatch.setattr(computer_settings, "_is_hyprland", lambda: True)

    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class Res:
            returncode = 0
            stdout = ""
        return Res()

    monkeypatch.setattr(computer_settings.subprocess, "run", fake_run)

    computer_settings.switch_window()
    assert ["hyprctl", "dispatch", "cyclenext"] in calls

    computer_settings.close_window()
    assert ["hyprctl", "dispatch", "killactive"] in calls

    computer_settings.close_app()
    assert calls.count(["hyprctl", "dispatch", "killactive"]) == 2

    computer_settings.full_screen()
    assert ["hyprctl", "dispatch", "fullscreen", "0"] in calls

    # Snapping queries the monitor geometry (see test_gate_fixes_v021 for the
    # exact half-screen batch); it must never fall back to movewindow l/r.
    computer_settings.snap_left()
    computer_settings.snap_right()
    assert ["hyprctl", "monitors", "-j"] in calls
    assert not any("movewindow" in c for c in calls)

    computer_settings.sleep_display()
    assert ["hyprctl", "dispatch", "dpms", "off"] in calls
