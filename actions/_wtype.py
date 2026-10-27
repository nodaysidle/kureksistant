"""Wayland keyboard input via `wtype` (shared by computer_control / computer_settings).

pyautogui drives X11 only, so on a native Hyprland session its keystrokes never
reach Wayland windows. When WAYLAND_DISPLAY is set and `wtype` is on PATH these
helpers build and run the equivalent wtype argv; callers fall back to pyautogui
when `available()` is False. Leading underscore keeps action_loader from
treating this as a tool.
"""
from __future__ import annotations

import os
import shutil
import subprocess

_MODIFIERS = {
    "ctrl": "ctrl", "control": "ctrl", "ctl": "ctrl",
    "shift": "shift",
    "alt": "alt", "option": "alt", "opt": "alt",
    "altgr": "altgr",
    "win": "logo", "super": "logo", "command": "logo", "cmd": "logo",
    "meta": "logo", "logo": "logo",
}

# pyautogui key names -> xkb keysym names understood by `wtype -k`.
_KEYS = {
    "enter": "Return", "return": "Return", "tab": "Tab",
    "esc": "Escape", "escape": "Escape",
    "backspace": "BackSpace", "delete": "Delete", "del": "Delete",
    "insert": "Insert", "space": "space",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End",
    "pageup": "Prior", "pgup": "Prior", "pagedown": "Next", "pgdn": "Next",
    "capslock": "Caps_Lock", "printscreen": "Print", "prtsc": "Print",
    "volumeup": "XF86AudioRaiseVolume", "volumedown": "XF86AudioLowerVolume",
    "volumemute": "XF86AudioMute", "playpause": "XF86AudioPlay",
    "nexttrack": "XF86AudioNext", "prevtrack": "XF86AudioPrev",
    "-": "minus", "+": "plus", "=": "equal", "/": "slash", "\\": "backslash",
    ",": "comma", ".": "period", ";": "semicolon", "'": "apostrophe",
    "[": "bracketleft", "]": "bracketright", "`": "grave",
}


def available() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) and shutil.which("wtype") is not None


def keysym(key: str) -> str:
    k = key.strip()
    low = k.lower()
    if low in _KEYS:
        return _KEYS[low]
    if len(low) in (2, 3) and low[0] == "f" and low[1:].isdigit():
        return low.upper()
    return k


def text_argv(text: str) -> list[str]:
    return ["wtype", "--", text]


def key_argv(key: str) -> list[str]:
    return ["wtype", "-k", keysym(key)]


def hotkey_argv(*keys: str) -> list[str]:
    mods = [_MODIFIERS[k.lower()] for k in keys if k.lower() in _MODIFIERS]
    rest = [k for k in keys if k.lower() not in _MODIFIERS]
    argv = ["wtype"]
    for m in mods:
        argv += ["-M", m]
    for k in rest:
        argv += ["-k", keysym(k)]
    for m in reversed(mods):
        argv += ["-m", m]
    return argv


def run(argv: list[str], timeout: float = 30.0) -> str | None:
    """Run a wtype argv. Returns None on success, else an error string."""
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return "wtype is not installed"
    except subprocess.TimeoutExpired:
        return "wtype timed out"
    if res.returncode != 0:
        return (res.stderr or "").strip() or f"wtype exited with code {res.returncode}"
    return None
