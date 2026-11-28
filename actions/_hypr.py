"""Hyprland window-manager dispatch shared by open_app / computer_control / computer_settings.

Hyprland >= 0.55 with a Lua config no longer parses `hyprctl dispatch focuswindow ...`;
it prints `error: ...` and still exits 0, so the exit code cannot be trusted. Leading
underscore keeps action_loader from treating this as a tool.
"""
from __future__ import annotations

import subprocess


def _reply(cmd: list[str]) -> str | None:
    """hyprctl stdout (it prints `error:`/`warning:` there), or None when hyprctl could not be run or exited non-zero."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return (res.stdout or "").strip().lower()


def _accepted(out: str | None) -> bool:
    # "invalid" is what a pre-Lua Hyprland answers to a Lua expression.
    return out is not None and not out.startswith(("error", "invalid")) and "not found" not in out


def hypr_dispatch(lua_expr: str, legacy: list[str]) -> bool:
    """Run a Hyprland dispatcher; True only if Hyprland accepted and ran it.

    Tries the Lua form first (`lua_expr`, e.g. 'hl.dsp.focus({ workspace = 3 })'), then
    the old-style argv (`legacy`, e.g. ['workspace', '3']) for non-Lua Hyprland."""
    out = _reply(["hyprctl", "dispatch", lua_expr])
    if _accepted(out):
        return True
    if out is not None and out.startswith("warning") and "not found" in out:
        return False  # it parsed and ran; the target is gone, the legacy form would find nothing either
    return _accepted(_reply(["hyprctl", "dispatch", *legacy]))
