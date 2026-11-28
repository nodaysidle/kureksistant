"""Unit tests verifying terminal and Omarchy/Hyprland fallbacks."""
from __future__ import annotations

import types


from actions import open_app, computer_settings


def test_linux_terminal_fallbacks_start_with_monstar_then_ghostty():
    assert open_app._LINUX_TERMINAL_FALLBACKS[:2] == ["monstar", "ghostty"]
    assert "herdr" not in open_app._LINUX_TERMINAL_FALLBACKS


def test_app_aliases_include_terminals_herdr_fish():
    assert "ghostty" in open_app._APP_ALIASES
    assert open_app._APP_ALIASES["ghostty"]["Linux"] == "ghostty"

    assert "herdr" in open_app._APP_ALIASES
    assert open_app._APP_ALIASES["herdr"]["Linux"] == "herdr"

    assert "fish" in open_app._APP_ALIASES
    assert open_app._APP_ALIASES["fish"]["Linux"] == "fish"


def test_computer_settings_hyprland_dispatch(monkeypatch):
    monkeypatch.setattr(computer_settings, "_OS", "Linux")
    monkeypatch.setattr(computer_settings, "_is_hyprland", lambda: True)

    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class Res:
            returncode = 0
            # Snapping needs a monitor; a failed snap now raises instead of passing silently.
            stdout = '[{"focused": true, "width": 2560, "height": 1440, "x": 0, "y": 0}]' if cmd[1] == "monitors" else ""
        return Res()

    monkeypatch.setattr(computer_settings.subprocess, "run", fake_run)

    computer_settings.switch_window()
    assert ["hyprctl", "dispatch", "hl.dsp.window.cycle_next()"] in calls

    computer_settings.close_window()
    assert ["hyprctl", "dispatch", "hl.dsp.window.close()"] in calls

    computer_settings.close_app()
    assert calls.count(["hyprctl", "dispatch", "hl.dsp.window.close()"]) == 2

    computer_settings.full_screen()
    assert ["hyprctl", "activewindow", "-j"] in calls

    # Snapping queries the monitor geometry (see test_gate_fixes_v021 for the
    # exact half-screen batch); it must never fall back to movewindow l/r.
    computer_settings.snap_left()
    computer_settings.snap_right()
    assert ["hyprctl", "monitors", "-j"] in calls
    assert not any("movewindow" in c for c in calls)

    computer_settings.sleep_display()
    assert ["hyprctl", "dispatch", 'hl.dsp.dpms({ action = "disable" })'] in calls


def _stub_launcher(monkeypatch, installed):
    launched = []
    monkeypatch.setattr(open_app, "_focus_hyprland_app", lambda name: False)
    monkeypatch.setattr(open_app.shutil, "which", lambda b: f"/usr/bin/{b}" if b in installed else None)
    monkeypatch.setattr(open_app.time, "sleep", lambda s: None)
    monkeypatch.setattr(open_app.subprocess, "Popen", lambda argv, **kw: launched.append(argv))
    return launched


def test_open_terminal_follows_the_default_terminal(monkeypatch):
    launched = _stub_launcher(monkeypatch, {"xdg-terminal-exec", "herdr", "foot"})
    assert open_app._launch_linux("terminal") is True
    assert launched == [["/usr/bin/xdg-terminal-exec"]]


def test_open_fish_runs_it_in_the_default_terminal(monkeypatch):
    launched = _stub_launcher(monkeypatch, {"xdg-terminal-exec", "fish"})
    assert open_app._launch_linux("fish") is True
    assert launched == [["/usr/bin/xdg-terminal-exec", "/usr/bin/fish"]]


def test_open_terminal_falls_back_when_there_is_no_default_launcher(monkeypatch):
    launched = _stub_launcher(monkeypatch, {"monstar", "ghostty"})
    assert open_app._launch_linux("terminal") is True
    assert launched == [["monstar"]]


def test_open_ghostty_launches_ghostty_itself(monkeypatch):
    launched = _stub_launcher(monkeypatch, {"xdg-terminal-exec", "ghostty"})
    assert open_app._launch_linux("ghostty") is True
    assert launched == [["/usr/bin/ghostty"]]


def test_open_btop_and_htop_run_in_the_default_terminal(monkeypatch):
    launched = _stub_launcher(monkeypatch, {"xdg-terminal-exec", "btop", "htop"})
    assert open_app._launch_linux("btop") is True
    assert open_app._launch_linux("htop") is True
    assert launched == [
        ["/usr/bin/xdg-terminal-exec", "/usr/bin/btop"],
        ["/usr/bin/xdg-terminal-exec", "/usr/bin/htop"],
    ]


def test_open_missing_tui_does_not_claim_success(monkeypatch):
    launched = _stub_launcher(monkeypatch, {"xdg-terminal-exec"})
    monkeypatch.setattr(open_app, "_find_linux_desktop_file", lambda n: None)
    monkeypatch.setattr(open_app.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1, stdout=""))
    assert open_app._launch_linux("htop") is False
    assert launched == []


def test_task_manager_maps_to_btop():
    assert open_app._normalize("task manager") == "btop"


def test_new_app_aliases(monkeypatch):
    assert open_app._normalize("craft anything") == "craft-anything-app"
    assert open_app._normalize("cutcap") == "cutcap open"
    assert open_app._normalize("nodaysidle browser") == "nodaysidle-browser"
    assert open_app._normalize("monstar") == "monstar"


def test_cutcap_alias_launches_with_its_argument(monkeypatch):
    launched = _stub_launcher(monkeypatch, {"cutcap"})
    assert open_app._launch_linux("cutcap open") is True
    assert launched == [["cutcap", "open"]]


def test_website_aliases_open_the_url_not_firefox(monkeypatch):
    for name, host in (("netflix", "netflix"), ("instagram", "instagram"), ("tiktok", "tiktok")):
        target = open_app._normalize(name)
        assert target.startswith("https://") and host in target
    runs = []
    _stub_launcher(monkeypatch, set())
    monkeypatch.setattr(open_app, "_find_linux_desktop_file", lambda n: None)
    monkeypatch.setattr(open_app.subprocess, "run", lambda cmd, **kw: runs.append(cmd) or types.SimpleNamespace(returncode=0 if cmd[0] == "xdg-open" else 1, stdout=""))
    assert open_app._launch_linux(open_app._normalize("netflix")) is True
    assert ["xdg-open", "https://www.netflix.com"] in runs


def test_safari_opens_the_default_browser(monkeypatch):
    assert open_app._normalize("safari") == "default-browser"
    runs = []
    _stub_launcher(monkeypatch, set())
    def fake_run(cmd, **kw):
        runs.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="helium.desktop\n")
    monkeypatch.setattr(open_app.subprocess, "run", fake_run)
    assert open_app._launch_linux("default-browser") is True
    assert ["gtk-launch", "helium.desktop"] in runs


def test_file_manager_names_open_the_home_folder(monkeypatch):
    home = str(open_app.Path.home())
    assert open_app._normalize("file explorer") == home
    assert open_app._normalize("finder") == home
    runs = []
    _stub_launcher(monkeypatch, set())
    monkeypatch.setattr(open_app, "_find_linux_desktop_file", lambda n: None)
    monkeypatch.setattr(open_app.subprocess, "run", lambda cmd, **kw: runs.append(cmd) or types.SimpleNamespace(returncode=0 if cmd[0] == "xdg-open" else 1, stdout=""))
    assert open_app._launch_linux(home) is True
    assert ["xdg-open", home] in runs


def test_task_manager_action_prefers_the_default_terminal(monkeypatch):
    launched = []
    monkeypatch.setattr(computer_settings, "_OS", "Linux")
    monkeypatch.setattr(computer_settings.shutil, "which", lambda b: b if b in {"xdg-terminal-exec", "ghostty", "btop"} else None)
    monkeypatch.setattr(computer_settings.subprocess, "Popen", lambda argv, **kw: launched.append(argv))
    computer_settings.open_task_manager()
    assert launched == [["xdg-terminal-exec", "-e", "btop"]]
