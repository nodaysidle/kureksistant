"""Hardening / Wayland-correctness tests for actions/ (v0.2.2)."""
from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from actions import _wtype, computer_control, computer_settings
from actions import file_controller, file_processor, send_message, weather_report


# ── file_controller.rename_file ────────────────────────────────────────────────

@pytest.fixture
def safe_root(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(file_controller, "_SAFE_ROOTS", [root])
    monkeypatch.setattr(file_controller, "push_undo", lambda *a, **k: None)
    return root


def test_rename_within_folder_works(safe_root):
    (safe_root / "a.txt").write_text("x")
    out = file_controller.rename_file(str(safe_root), "a.txt", "b.txt")
    assert out.startswith("Renamed")
    assert (safe_root / "b.txt").read_text() == "x"
    assert not (safe_root / "a.txt").exists()


@pytest.mark.parametrize("bad", ["../escaped.txt", "sub/x.txt", "..\\x.txt", "..", "."])
def test_rename_rejects_paths(safe_root, bad):
    (safe_root / "a.txt").write_text("x")
    out = file_controller.rename_file(str(safe_root), "a.txt", bad)
    assert "plain file name" in out
    assert (safe_root / "a.txt").exists()
    assert not (safe_root.parent / "escaped.txt").exists()


# ── file_processor archive extraction ─────────────────────────────────────────

def _make_tar(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as t:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))


def test_tar_traversal_member_not_written_outside_dest(tmp_path):
    arc = tmp_path / "evil.tar"
    _make_tar(arc, {"../evil.txt": b"pwned"})
    dest = tmp_path / "out" / "dest"
    dest.mkdir(parents=True)
    out = file_processor._process_archive(arc, "extract", {"destination": str(dest)})
    assert not (dest.parent / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()
    assert "Extract failed" in out


def test_tar_normal_extract(tmp_path):
    arc = tmp_path / "ok.tar"
    _make_tar(arc, {"dir/hello.txt": b"hi"})
    dest = tmp_path / "dest"
    out = file_processor._process_archive(arc, "extract", {"destination": str(dest)})
    assert out == f"Extracted to: {dest}"
    assert (dest / "dir" / "hello.txt").read_bytes() == b"hi"


# ── send_message honest wording ───────────────────────────────────────────────

class _FakeGui:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    def _rec(self, *a, **k):
        if self.fail:
            raise RuntimeError("display gone")
        self.calls.append(a)

    press = hotkey = write = _rec


@pytest.fixture
def fake_send(monkeypatch):
    def _setup(fail=False):
        gui = _FakeGui(fail)
        monkeypatch.setattr(send_message, "pyautogui", gui, raising=False)
        monkeypatch.setattr(send_message, "_PYAUTOGUI", True)
        monkeypatch.setattr(send_message, "_PYPERCLIP", False)
        monkeypatch.setattr(send_message, "_open_app", lambda name: True)
        monkeypatch.setattr(send_message.time, "sleep", lambda *_: None)
        return gui
    return _setup


def test_send_message_does_not_claim_delivery(fake_send):
    fake_send()
    out = send_message.send_message({"receiver": "Ana", "message_text": "hi", "platform": "telegram"})
    assert out == ("Typed the message to Ana in Telegram and pressed Enter — "
                   "I can't confirm delivery.")
    assert "sent" not in out.lower()


def test_send_message_reports_typing_failure(fake_send):
    fake_send(fail=True)
    out = send_message.send_message({"receiver": "Ana", "message_text": "hi", "platform": "whatsapp"})
    assert out.startswith("Failed while typing the message to Ana in WhatsApp")


# ── weather_report ────────────────────────────────────────────────────────────

_J1 = {
    "current_condition": [{
        "temp_C": "18", "FeelsLikeC": "17", "humidity": "62",
        "windspeedKmph": "11", "winddir16Point": "NW",
        "weatherDesc": [{"value": "Partly cloudy"}],
    }],
    "weather": [
        {"date": "2026-10-20", "mintempC": "12", "maxtempC": "21",
         "hourly": [{"time": "1200", "weatherDesc": [{"value": "Sunny"}]}]},
        {"date": "2026-10-21", "mintempC": "10", "maxtempC": "16",
         "hourly": [{"time": "900", "weatherDesc": [{"value": "Rain"}]},
                    {"time": "1200", "weatherDesc": [{"value": "Light rain"}]}]},
    ],
}


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_weather_report_summarises_j1(monkeypatch):
    seen = {}

    def fake_get(url, timeout=None, headers=None):
        seen["url"], seen["timeout"] = url, timeout
        return _Resp(_J1)

    monkeypatch.setattr(weather_report.requests, "get", fake_get)
    out = weather_report.weather_action({"city": "São Paulo"})
    assert seen["url"] == "https://wttr.in/S%C3%A3o%20Paulo?format=j1"
    assert seen["timeout"] == 8
    for frag in ("partly cloudy", "18 degrees", "feels like 17", "humidity 62",
                 "wind 11", "low is 12", "high is 21"):
        assert frag in out


def test_weather_report_tomorrow(monkeypatch):
    monkeypatch.setattr(weather_report.requests, "get", lambda *a, **k: _Resp(_J1))
    out = weather_report.weather_action({"city": "Oslo", "time": "tomorrow"})
    assert out.startswith("Tomorrow in Oslo: light rain, between 10 and 16")


def test_weather_report_network_error(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("no route")

    monkeypatch.setattr(weather_report.requests, "get", boom)
    out = weather_report.weather_action({"city": "Oslo"})
    assert "couldn't fetch the weather for Oslo" in out


# ── wtype argv construction ───────────────────────────────────────────────────

@pytest.fixture
def wayland(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(_wtype.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(_wtype.subprocess, "run", fake_run)
    monkeypatch.setattr(computer_control.time, "sleep", lambda *_: None)
    return calls


def test_wtype_hotkey_argv(wayland):
    assert computer_control._hotkey("ctrl", "shift", "t") == "Hotkey: ctrl+shift+t"
    assert wayland == [["wtype", "-M", "ctrl", "-M", "shift", "-k", "t",
                        "-m", "shift", "-m", "ctrl"]]


def test_wtype_text_argv(wayland):
    computer_control._type("-rf hello")
    assert wayland == [["wtype", "--", "-rf hello"]]


def test_wtype_press_maps_key_names(wayland):
    computer_control._press("enter")
    assert wayland == [["wtype", "-k", "Return"]]


def test_close_tab_sends_ctrl_w_via_wtype(wayland, monkeypatch):
    monkeypatch.setattr(computer_settings, "_OS", "Linux")
    computer_settings.close_tab()
    assert wayland == [["wtype", "-M", "ctrl", "-k", "w", "-m", "ctrl"]]


def test_wtype_failure_is_reported(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(_wtype.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(_wtype.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "no seat"))
    assert computer_control._press("enter") == "Pressing enter failed (wtype): no seat"


def test_pyautogui_used_without_wayland(monkeypatch):
    keys = []
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(computer_control, "_PYAUTOGUI", True)
    monkeypatch.setattr(computer_control.pyautogui, "hotkey", lambda *k: keys.append(k))
    computer_control._hotkey("ctrl", "c")
    assert keys == [("ctrl", "c")]
