#computer_settings.py
import os
import re
import shutil
import sys
import time
import subprocess
import platform
from pathlib import Path

def _is_hyprland() -> bool:
    return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or shutil.which("hyprctl"))

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

from core import confirm
from core.undo import push_undo
from actions import _wtype

_OS = platform.system()  # "Darwin" | "Linux"


def _hk(*keys) -> None:
    """Send a key chord: wtype on Wayland, pyautogui elsewhere."""
    if _wtype.available():
        err = _wtype.run(_wtype.hotkey_argv(*keys))
        if err:
            raise RuntimeError(f"wtype {'+'.join(keys)} failed: {err}")
        return
    pyautogui.hotkey(*keys)


def _key(key: str) -> None:
    """Press a single key: wtype on Wayland, pyautogui elsewhere."""
    if _wtype.available():
        err = _wtype.run(_wtype.key_argv(key))
        if err:
            raise RuntimeError(f"wtype key {key} failed: {err}")
        return
    pyautogui.press(key)


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_api_key() -> str:
    from memory.config_manager import get_gemini_key
    return get_gemini_key()

def _get_macos_wifi_interface() -> str:
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if "Wi-Fi" in line or "AirPort" in line:
                for j in range(i, min(i + 4, len(lines))):
                    if lines[j].startswith("Device:"):
                        return lines[j].split(":", 1)[1].strip()
    except Exception:
        pass
    return "en0"

def volume_up():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            "set volume output volume (output volume of (get volume settings) + 10)"],
            capture_output=True, timeout=10)
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"],
            capture_output=True, timeout=10)

def volume_down():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            "set volume output volume (output volume of (get volume settings) - 10)"],
            capture_output=True, timeout=10)
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"],
            capture_output=True, timeout=10)

def volume_mute():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e", "set volume with output muted"],
            capture_output=True, timeout=10)
    else:
        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
            capture_output=True, timeout=10)

def volume_get() -> int | None:
    """Current master volume 0-100, or None if this platform will not say.

    Undo needs a "before" value, and reading one is cheap on every OS we
    support. Where it is not readable the action simply is not registered as
    undoable — a wrong undo is worse than no undo."""
    try:
        if _OS == "Darwin":
            r = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"],
                               capture_output=True, text=True, timeout=5)
            return max(0, min(100, int(r.stdout.strip())))
        r = subprocess.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                           capture_output=True, text=True, timeout=5)
        m = re.search(r"(\d+)%", r.stdout)
        return max(0, min(100, int(m.group(1)))) if m else None
    except Exception:
        return None


def brightness_get() -> int | None:
    """Current brightness 0-100, or None where it cannot be read."""
    try:
        if _OS == "Linux" and shutil.which("brightnessctl"):
            cur = int(subprocess.run(["brightnessctl", "get"],
                                     capture_output=True, text=True, timeout=5).stdout.strip())
            mx  = int(subprocess.run(["brightnessctl", "max"],
                                     capture_output=True, text=True, timeout=5).stdout.strip())
            return max(0, min(100, round(cur * 100 / mx))) if mx else None
    except Exception:
        pass
    return None


def brightness_set(value: int) -> None:
    """Set brightness to an absolute percentage. Only used to restore a value
    captured before a change, so it is undo's counterpart to the up/down pair."""
    value = max(0, min(100, int(value)))
    if _OS == "Linux" and shutil.which("brightnessctl"):
        subprocess.run(["brightnessctl", "set", f"{value}%"], capture_output=True, timeout=5)


def volume_set(value: int):
    value = max(0, min(100, int(value)))
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e", f"set volume output volume {value}"],
            capture_output=True, timeout=10)
        return
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{value}%"],
            capture_output=True, timeout=10)
        return

def brightness_up():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 144'],
            capture_output=True, timeout=10)
    elif _OS == "Linux":
        if not shutil.which("brightnessctl"):
            raise RuntimeError("brightnessctl is not installed; cannot change brightness")
        res = subprocess.run(["brightnessctl", "set", "+10%"], capture_output=True, text=True, timeout=5)
        if res.returncode != 0:
            raise RuntimeError(f"brightnessctl failed: {(res.stderr or '').strip()}")

def brightness_down():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 145'],
            capture_output=True, timeout=10)
    elif _OS == "Linux":
        if not shutil.which("brightnessctl"):
            raise RuntimeError("brightnessctl is not installed; cannot change brightness")
        res = subprocess.run(["brightnessctl", "set", "10%-"], capture_output=True, text=True, timeout=5)
        if res.returncode != 0:
            raise RuntimeError(f"brightnessctl failed: {(res.stderr or '').strip()}")

def close_app():
    if _OS == "Darwin":
        _hk("command", "q")
    elif _OS == "Linux" and _is_hyprland():
        subprocess.run(["hyprctl", "dispatch", "killactive"], capture_output=True, timeout=10)
    else:
        _hk("alt", "f4")

def close_window():
    """Explicit 'close the window' request. On Hyprland this closes the whole
    active window (killactive). Tab/document closes go through close_tab()."""
    if _OS == "Darwin":
        _hk("command", "w")
    elif _OS == "Linux" and _is_hyprland():
        subprocess.run(["hyprctl", "dispatch", "killactive"], capture_output=True, timeout=10)
    else:
        _hk("ctrl", "w")


def _hypr_toggle_fullscreen() -> None:
    """Toggle true fullscreen on the active Hyprland window.

    Reads the current state from `hyprctl activewindow -j` ("fullscreen" is an
    int mode on Hyprland >= 0.42 where 2 = fullscreen, or a bool on older
    versions) and sets the opposite state explicitly with `fullscreenstate`,
    so "full screen" turns it on and asking again turns it off."""
    import json as _json
    try:
        res = subprocess.run(["hyprctl", "activewindow", "-j"],
                             capture_output=True, text=True, timeout=2.0)
        win = _json.loads(res.stdout or "{}")
        state = win.get("fullscreen", 0)
        is_full = state is True or (isinstance(state, int)
                                    and not isinstance(state, bool) and state >= 2)
        target = "0" if is_full else "2"
        subprocess.run(["hyprctl", "dispatch", "fullscreenstate", target, "-1"],
                       capture_output=True, timeout=2.0)
    except Exception as e:
        print(f"[computer_settings] Hyprland fullscreen toggle failed: {e}")
        _key("f11")

def full_screen():
    if _OS == "Darwin":
        _hk("ctrl", "command", "f")
    elif _OS == "Linux" and _is_hyprland():
        _hypr_toggle_fullscreen()
    else:
        _key("f11")

def minimize_window():
    if _OS == "Darwin": _hk("command", "m")
    else:               _hk("win", "down")

def maximize_window():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to keystroke "f" '
            'using {control down, command down}'],
            capture_output=True, timeout=10)
    elif _OS == "Linux" and _is_hyprland():
        subprocess.run(["hyprctl", "dispatch", "fullscreen", "1"], capture_output=True, timeout=10)
    else:
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-b", "add,maximized_vert,maximized_horz"],
                capture_output=True, timeout=10)
        except Exception:
            _hk("super", "up")


def _hypr_snap_half(side: str) -> bool:
    """Snap the active window to the left/right half of its monitor on Hyprland:
    make it floating, then size and place it exactly (logical pixels, minus
    reserved bar areas)."""
    import json as _json
    try:
        res = subprocess.run(["hyprctl", "monitors", "-j"],
                             capture_output=True, text=True, timeout=2.0)
        mons = _json.loads(res.stdout or "[]")
        mon = next((m for m in mons if m.get("focused")), mons[0] if mons else None)
        if not mon:
            return False
        scale = float(mon.get("scale") or 1.0) or 1.0
        rl, rt, rr, rb = (list(mon.get("reserved") or []) + [0, 0, 0, 0])[:4]
        width = int(int(mon["width"]) / scale) - rl - rr
        height = int(int(mon["height"]) / scale) - rt - rb
        half = width // 2
        x = int(mon.get("x", 0)) + rl + (half if side == "right" else 0)
        y = int(mon.get("y", 0)) + rt
        batch = (f"dispatch setfloating active ; "
                 f"dispatch resizeactive exact {half} {height} ; "
                 f"dispatch moveactive exact {x} {y}")
        subprocess.run(["hyprctl", "--batch", batch], capture_output=True, timeout=2.0)
        return True
    except Exception as e:
        print(f"[computer_settings] Hyprland snap failed: {e}")
        return False

def snap_left():
    if _OS == "Darwin":
        # macOS has no built-in snap; try Rectangle app shortcut if installed
        try:
            subprocess.run(["open", "-a", "Rectangle"], capture_output=True, timeout=1)
        except Exception:
            pass
        _hk("ctrl", "option", "left")
    elif _OS == "Linux" and _is_hyprland():
        _hypr_snap_half("left")
    else:  # Linux
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,0,0,960,1080"],
                capture_output=True, timeout=10)
        except Exception:
            pass

def snap_right():
    if _OS == "Darwin":
        try:
            subprocess.run(["open", "-a", "Rectangle"], capture_output=True, timeout=1)
        except Exception:
            pass
        _hk("ctrl", "option", "right")
    elif _OS == "Linux" and _is_hyprland():
        _hypr_snap_half("right")
    else:  # Linux
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,960,0,960,1080"],
                capture_output=True, timeout=10)
        except Exception:
            pass

def switch_window():
    if _OS == "Darwin":
        _hk("command", "tab")
    elif _OS == "Linux" and _is_hyprland():
        subprocess.run(["hyprctl", "dispatch", "cyclenext"], capture_output=True, timeout=10)
    else:
        _hk("alt", "tab")

def show_desktop():
    if _OS == "Darwin":   _hk("fn", "f11")
    else:                  _hk("super", "d")

def open_task_manager():
    if _OS == "Darwin":
        subprocess.Popen(["open", "-a", "Activity Monitor"])
    else:
        # Ghostty is the user's terminal; the rest are fallbacks
        for term in ["ghostty", "herdr", "alacritty", "kitty", "foot"]:
            if shutil.which(term):
                for mon in ["btop", "htop"]:
                    if shutil.which(mon):
                        subprocess.Popen([term, "-e", mon])
                        return
        for cmd in [["gnome-system-monitor"], ["xfce4-taskmanager"], ["htop"]]:
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd)
                break


def focus_search():
    if _OS == "Darwin": _hk("command", "l")
    else:               _hk("ctrl", "l")

def pause_video():      _key("space")

def refresh_page():
    if _OS == "Darwin": _hk("command", "r")
    else:               _key("f5")

def close_tab():
    if _OS == "Darwin": _hk("command", "w")
    else:               _hk("ctrl", "w")

def new_tab():
    if _OS == "Darwin": _hk("command", "t")
    else:               _hk("ctrl", "t")

def next_tab():
    if _OS == "Darwin": _hk("command", "shift", "bracketright")
    else:               _hk("ctrl", "tab")

def prev_tab():
    if _OS == "Darwin": _hk("command", "shift", "bracketleft")
    else:               _hk("ctrl", "shift", "tab")

def go_back():
    if _OS == "Darwin": _hk("command", "left")
    else:               _hk("alt", "left")

def go_forward():
    if _OS == "Darwin": _hk("command", "right")
    else:               _hk("alt", "right")

def zoom_in():
    if _OS == "Darwin": _hk("command", "equal")
    else:               _hk("ctrl", "equal")

def zoom_out():
    if _OS == "Darwin": _hk("command", "minus")
    else:               _hk("ctrl", "minus")

def zoom_reset():
    if _OS == "Darwin": _hk("command", "0")
    else:               _hk("ctrl", "0")

def find_on_page():
    if _OS == "Darwin": _hk("command", "f")
    else:               _hk("ctrl", "f")

def reload_page_n(n: int):
    for _ in range(max(1, n)):
        refresh_page()
        time.sleep(0.8)


def scroll_up(amount: int = 500):    pyautogui.scroll(amount)
def scroll_down(amount: int = 500):  pyautogui.scroll(-amount)

def scroll_top():
    if _OS == "Darwin": _hk("command", "up")
    else:               _hk("ctrl", "home")

def scroll_bottom():
    if _OS == "Darwin": _hk("command", "down")
    else:               _hk("ctrl", "end")

def page_up():   _key("pageup")
def page_down(): _key("pagedown")


def copy():
    if _OS == "Darwin": _hk("command", "c")
    else:               _hk("ctrl", "c")

def paste():
    if _OS == "Darwin": _hk("command", "v")
    else:               _hk("ctrl", "v")

def cut():
    if _OS == "Darwin": _hk("command", "x")
    else:               _hk("ctrl", "x")

def undo():
    if _OS == "Darwin": _hk("command", "z")
    else:               _hk("ctrl", "z")

def redo():
    if _OS == "Darwin": _hk("command", "shift", "z")
    else:               _hk("ctrl", "y")

def select_all():
    if _OS == "Darwin": _hk("command", "a")
    else:               _hk("ctrl", "a")

def save_file():
    if _OS == "Darwin": _hk("command", "s")
    else:               _hk("ctrl", "s")

def press_enter():   _key("enter")
def press_escape():  _key("escape")
def press_key(key: str): _key(key)

def type_text(text: str, press_enter_after: bool = False):
    if not text:
        return
    if _wtype.available():
        err = _wtype.run(_wtype.text_argv(str(text)))
        if err:
            raise RuntimeError(f"wtype typing failed: {err}")
    elif _PYPERCLIP:
        pyperclip.copy(str(text))
        time.sleep(0.15)
        paste()
    else:
        pyautogui.write(str(text), interval=0.03)
    if press_enter_after:
        time.sleep(0.1)
        _key("enter")

def take_screenshot():
    if _OS == "Darwin":
        _hk("command", "shift", "3")
    else:
        # Check Wayland grim first
        if shutil.which("grim"):
            pics_dir = Path.home() / "Pictures"
            if not pics_dir.exists():
                pics_dir = Path.home() / "Desktop"
                pics_dir.mkdir(parents=True, exist_ok=True)
            shot_file = pics_dir / f"screenshot_{int(time.time())}.png"
            res = subprocess.run(["grim", str(shot_file)], capture_output=True, timeout=10)
            if res.returncode == 0:
                return
        for cmd in [["scrot"], ["gnome-screenshot"], ["import", "-window", "root", "screenshot.png"]]:
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd)
                return
        _hk("ctrl", "print_screen")

def lock_screen():
    if _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True, timeout=10)
    else:
        for cmd in [
            ["hyprlock"],
            ["swaylock"],
            ["loginctl", "lock-session"],
            ["gnome-screensaver-command", "-l"],
            ["xdg-screensaver", "lock"],
        ]:
            if shutil.which(cmd[0]):
                subprocess.run(cmd, capture_output=True, timeout=10)
                return

def open_system_settings():
    if _OS == "Darwin":
        subprocess.Popen(["open", "-a", "System Preferences"])
    else:
        for cmd in [["gnome-control-center"], ["xfce4-settings-manager"], ["kcmshell5"]]:
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd)
                return

def open_file_explorer():
    if _OS == "Darwin":
        subprocess.Popen(["open", str(Path.home())])
    else:
        for cmd in [["nautilus"], ["thunar"], ["dolphin"], ["nemo"]]:
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd)
                return
        subprocess.Popen(["xdg-open", str(Path.home())])

def sleep_display():
    if _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True, timeout=10)
    elif _OS == "Linux" and _is_hyprland():
        subprocess.run(["hyprctl", "dispatch", "dpms", "off"], capture_output=True, timeout=10)
    else:
        subprocess.run(["xset", "dpms", "force", "off"], capture_output=True, timeout=10)

def open_run():
    raise RuntimeError("there is no Run dialog on this system; ask me to open the app by name instead")

def dark_mode():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell app "System Events" to tell appearance preferences '
            'to set dark mode to not dark mode'],
            capture_output=True, timeout=10)
    else:
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True, text=True, timeout=10
            )
            current = result.stdout.strip()
            new_scheme = "'default'" if "dark" in current else "'prefer-dark'"
            subprocess.run(
                ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", new_scheme],
                capture_output=True, timeout=10
            )
        except Exception as e:
            print(f"[Settings] dark_mode Linux failed: {e}")

def toggle_wifi():
    if _OS == "Darwin":
        iface = _get_macos_wifi_interface()
        result = subprocess.run(
            ["networksetup", "-getairportpower", iface],
            capture_output=True, text=True, timeout=10
        )
        state = "off" if "On" in result.stdout else "on"
        subprocess.run(["networksetup", "-setairportpower", iface, state],
            capture_output=True, timeout=10)
    else:
        try:
            result = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True, timeout=10)
            state  = "off" if "enabled" in result.stdout else "on"
            subprocess.run(["nmcli", "radio", "wifi", state], capture_output=True, timeout=10)
        except Exception as e:
            print(f"[Settings] toggle_wifi Linux failed: {e}")

def restart_computer():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to restart'],
            capture_output=True, timeout=10)
    else:
        subprocess.run(["systemctl", "reboot"], capture_output=True, timeout=10)

def shutdown_computer():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to shut down'],
            capture_output=True, timeout=10)
    else:
        subprocess.run(["systemctl", "poweroff"], capture_output=True, timeout=10)

ACTION_MAP: dict[str, callable] = {
    "volume_up":           volume_up,
    "volume_down":         volume_down,
    "mute":                volume_mute,
    "unmute":              volume_mute,
    "toggle_mute":         volume_mute,
    "brightness_up":       brightness_up,
    "brightness_down":     brightness_down,
    "sleep_display":       sleep_display,
    "screen_off":          sleep_display,
    "pause_video":         pause_video,
    "play_pause":          pause_video,
    "close_app":           close_app,
    "close_window":        close_window,
    "full_screen":         full_screen,
    "fullscreen":          full_screen,
    "minimize":            minimize_window,
    "maximize":            maximize_window,
    "snap_left":           snap_left,
    "snap_right":          snap_right,
    "switch_window":       switch_window,
    "show_desktop":        show_desktop,
    "task_manager":        open_task_manager,
    "focus_search":        focus_search,
    "refresh_page":        refresh_page,
    "reload":              refresh_page,
    "close_tab":           close_tab,
    "new_tab":             new_tab,
    "next_tab":            next_tab,
    "prev_tab":            prev_tab,
    "go_back":             go_back,
    "go_forward":          go_forward,
    "zoom_in":             zoom_in,
    "zoom_out":            zoom_out,
    "zoom_reset":          zoom_reset,
    "find_on_page":        find_on_page,
    "scroll_up":           scroll_up,
    "scroll_down":         scroll_down,
    "scroll_top":          scroll_top,
    "scroll_bottom":       scroll_bottom,
    "page_up":             page_up,
    "page_down":           page_down,
    "copy":                copy,
    "paste":               paste,
    "cut":                 cut,
    "undo":                undo,
    "redo":                redo,
    "select_all":          select_all,
    "save":                save_file,
    "enter":               press_enter,
    "escape":              press_escape,
    "screenshot":          take_screenshot,
    "lock_screen":         lock_screen,
    "open_settings":       open_system_settings,
    "file_explorer":       open_file_explorer,
    "open_run":            open_run,
    "dark_mode":           dark_mode,
    "toggle_wifi":         toggle_wifi,
    "restart":             restart_computer,
    "shutdown":            shutdown_computer,
}

# ── What needs a human, and what just needs an undo ──────────────────────────
#
# The old gate was `_DANGEROUS_ACTIONS = {"restart", "shutdown"}` checked against
# a `confirmed` parameter the MODEL filled in — so the model confirmed its own
# shutdowns, and everything else (switching off the WiFi this assistant is
# talking over) had no gate at all.
#
# Two lists now, and the split is about reversibility, not about how alarming
# the word sounds:
#
#   _IRREVERSIBLE — a human presses a button on the HUD. Nothing else.
#   everything else — done immediately, with an undo pushed if it can be undone.
#
# Asking before every action is what makes an assistant unusable, and every
# question costs a round trip. Undo is both faster and safer than a prompt.
_IRREVERSIBLE = {
    "restart":     ("Restart this computer",
                    "Anything unsaved will be lost. The computer restarts in 10 seconds."),
    "shutdown":    ("Shut this computer down",
                    "Anything unsaved will be lost. The computer powers off in 10 seconds."),
    # Not obviously destructive, and that is exactly why it was missed: turning
    # the WiFi off cuts the assistant's own connection to the Live API, so it
    # cannot be asked to turn it back on.
    "toggle_wifi": ("Switch WiFi off or on",
                    "If this switches WiFi off, JARVIS loses its connection and "
                    "cannot switch it back on by voice."),
}

# Kept so anything still importing the old name keeps working.
_DANGEROUS_ACTIONS = set(_IRREVERSIBLE)


# ── Local intent resolution ──────────────────────────────────────────────────
#
# This used to be an entire extra Gemini call, made INSIDE the tool: the Live
# model called computer_settings, and computer_settings then asked a second
# model which action was meant. Every "turn the volume down" paid for two round
# trips — and when that second call failed, the fallback was
# `description.lower().replace(" ", "_")`, which turns the Turkish for "turn it
# down" into `sesi_kis`, i.e. straight to "Unknown action".
#
# Nothing here needs a language model. The Live model already understands the
# sentence; it only needed the vocabulary, which the tool declaration now spells
# out in full. What is left is spelling tolerance, and difflib does that in
# microseconds instead of ~600 ms and a quota unit.
_ALIASES = {
    "volume_up":       ("louder", "raise volume", "turn it up", "increase volume"),
    "volume_down":     ("quieter", "lower volume", "turn it down", "decrease volume"),
    "mute":            ("silence", "sound off", "no sound"),
    "brightness_up":   ("brighter", "raise brightness", "increase brightness"),
    "brightness_down": ("dimmer", "dim", "lower brightness", "decrease brightness"),
    # close_window first: "close this window" also contains "close this".
    "close_window":    ("close window", "close this window", "close the window"),
    "close_tab":       ("close this", "close it", "close tab", "close this tab",
                        "close the tab"),
    "full_screen":     ("fullscreen", "maximise screen"),
    "show_desktop":    ("minimise everything", "go to desktop"),
    "lock_screen":     ("lock", "lock the pc", "lock computer"),
    "sleep_display":   ("screen off", "turn off the screen", "display off"),
    "dark_mode":       ("night mode", "light mode", "toggle theme"),
    "toggle_wifi":     ("wifi", "wi-fi", "internet off", "internet on"),
    "task_manager":    ("processes", "task list"),
    "screenshot":      ("capture screen", "take a screenshot", "snip"),
    "refresh_page":    ("refresh", "reload page"),
    "new_tab":         ("open a tab", "open new tab"),
    "shutdown":        ("power off", "turn off the computer", "switch off the pc"),
    "restart":         ("reboot", "restart the pc"),
}

_VALUE_ACTIONS = {"volume_set", "type_text", "press_key", "reload_n",
                  "scroll_up", "scroll_down"}


def _normalise(text: str) -> str:
    return (text or "").strip().lower().replace("-", "_").replace(" ", "_")


def _detect_action(description: str) -> dict:
    """Resolve a free-text description to an action name, locally.

    Returns {"action": name, "value": ...}; `action` is "" when nothing matched,
    which the caller turns into a message naming real candidates — one round
    trip, and only in the case that used to cost one anyway."""
    raw  = (description or "").strip()
    norm = _normalise(raw)
    if not norm:
        return {"action": "", "value": None}

    known = set(ACTION_MAP) | _VALUE_ACTIONS

    # 1. Already an action name.
    if norm in known:
        return {"action": norm, "value": None}

    low = raw.lower()

    # 2. "set volume to 30", "sesi 30 yap" — a number next to a volume word.
    num = re.search(r"(\d{1,3})\s*%?", low)
    if num and any(w in low for w in ("volume", "ses", "sound", "lautstark", "громкость")):
        return {"action": "volume_set", "value": max(0, min(100, int(num.group(1))))}

    # 3. Alias phrases.
    for action, phrases in _ALIASES.items():
        if any(_normalise(p) == norm or p in low for p in phrases):
            return {"action": action, "value": None}

    # 4. Fuzzy match on the action names — catches "fullscren", "volumeup".
    import difflib
    close = difflib.get_close_matches(norm, sorted(known), n=1, cutoff=0.72)
    if close:
        return {"action": close[0], "value": None}

    # 5. Substring: "increase_the_brightness" contains "brightness".
    for action in sorted(known, key=len, reverse=True):
        if len(action) > 4 and (action in norm or norm in action):
            return {"action": action, "value": None}

    return {"action": "", "value": None}


def _suggest(description: str) -> str:
    """What to tell the model when nothing matched. Names real actions so its
    retry lands, instead of the old 'Unknown action' dead end."""
    import difflib
    near = difflib.get_close_matches(_normalise(description),
                                     sorted(ACTION_MAP), n=5, cutoff=0.3)
    hint = ", ".join(near) if near else ", ".join(sorted(ACTION_MAP)[:12])
    return (f"I could not match '{description}' to a computer action. "
            f"Call computer_settings again with an exact `action` from: {hint}.")

def computer_settings(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    if not _PYAUTOGUI:
        return "pyautogui is not installed. Run: pip install pyautogui"

    params      = parameters or {}
    raw_action  = params.get("action", "").strip()
    description = params.get("description", "").strip()
    value       = params.get("value", None)

    if not raw_action and description:
        detected   = _detect_action(description)
        raw_action = detected.get("action", "")
        if value is None:
            value = detected.get("value")

    action = raw_action.lower().strip().replace(" ", "_").replace("-", "_")

    if not action:
        return _suggest(description or raw_action)

    print(f"[Settings] Action: {action}  Value: {value}  OS: {_OS}")
    if player:
        player.write_log(f"[Settings] {action}")

    # ── The gate ─────────────────────────────────────────────────────────────
    # A human presses a button, or this does not happen. The model can no longer
    # write its own permission slip, and the action itself is handed to the UI
    # rather than performed here — so returning early is not "declining", it is
    # "parked until someone says yes".
    if action in _IRREVERSIBLE:
        title, detail = _IRREVERSIBLE[action]
        func = ACTION_MAP.get(action)
        if func is None:
            return f"Unknown action: '{raw_action}'."
        if confirm.pending_title():
            return ("There is already a confirmation waiting on screen. "
                    "Ask the user to answer that one first.")
        return confirm.request(
            key=action, title=title, detail=detail,
            run=lambda f=func, a=action: (f(), f"{a} done.")[1],
        )

    if action == "volume_set":
        try:
            target = int(value if value is not None else 50)
            before = volume_get()
            volume_set(target)
            if before is not None:
                push_undo(f"volume {before}% → {target}%",
                          lambda b=before: (volume_set(b), f"Back to {b}%.")[1])
            return f"Volume set to {target}%."
        except Exception as e:
            return f"Could not set volume: {e}"

    if action in ("type_text", "write_on_screen", "type", "write"):
        text = str(value or params.get("text", "")).strip()
        if not text:
            return "No text provided to type."
        enter_after = str(params.get("press_enter", "false")).lower() in ("true", "1", "yes")
        type_text(text, press_enter_after=enter_after)
        return f"Typed: {text[:80]}"

    if action == "press_key":
        key = str(value or params.get("key", "")).strip()
        if not key:
            return "No key specified."
        press_key(key)
        return f"Pressed: {key}"

    if action in ("reload_n", "refresh_n", "reload_page_n"):
        try:
            reload_page_n(int(value or 1))
            return f"Reloaded {value or 1} time(s)."
        except Exception as e:
            return f"Reload failed: {e}"

    if action == "scroll_up":
        scroll_up(int(value or 500))
        return "Scrolled up."

    if action == "scroll_down":
        scroll_down(int(value or 500))
        return "Scrolled down."

    func = ACTION_MAP.get(action)
    if not func:
        return _suggest(raw_action or description)

    # ── Capture "before" so the change can be taken back ─────────────────────
    # Read-then-write is the whole mechanism for settings: there is no clever
    # inverse to compute, just a value to remember. Where the platform will not
    # tell us the current value, nothing is registered — an undo that restores
    # a guess is worse than no undo at all.
    _before = None
    if action in ("volume_up", "volume_down", "mute", "unmute", "toggle_mute"):
        _before = ("volume", volume_get())
    elif action in ("brightness_up", "brightness_down"):
        _before = ("brightness", brightness_get())

    try:
        func()
    except Exception as e:
        print(f"[Settings] Action failed ({action}): {e}")
        return f"Action failed ({action}): {e}"

    if _before:
        kind, old = _before
        if old is not None:
            if kind == "volume":
                push_undo(f"volume ({action})",
                          lambda b=old: (volume_set(b), f"Volume back to {b}%.")[1])
            elif kind == "brightness":
                push_undo(f"brightness ({action})",
                          lambda b=old: (brightness_set(b), f"Brightness back to {b}%.")[1])
    elif action == "dark_mode":
        # A pure toggle: calling it again is the undo.
        push_undo("dark mode toggled",
                  lambda: (dark_mode(), "Theme switched back.")[1])

    return f"Done: {action}."


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "computer_settings",
    "description": "Controls the computer: volume, brightness, window management, keyboard shortcuts, typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. Use for ANY single computer control command. restart, shutdown and toggle_wifi put a confirmation on the user's screen and do NOT happen until they press it — never claim they are done. Volume, brightness and dark mode can be reversed with the `undo` tool.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            # The exact vocabulary, spelled out.
            #
            # This used to say only "The action to perform", so the model
            # usually filled `description` instead — and computer_settings then
            # made a SECOND Gemini call, inside the tool, purely to translate
            # that sentence into one of these names. Every "turn the volume
            # down" cost two model round trips.
            #
            # Naming the actions here costs ~600 characters once per session and
            # removes a whole round trip from every computer command.
            "action": {
                "type": "STRING",
                "description": (
                    "The exact action. Prefer this over `description` — pick one of: "
                    "volume_up | volume_down | volume_set | mute | "
                    "brightness_up | brightness_down | sleep_display | "
                    "pause_video | close_app | close_window | full_screen | "
                    "minimize | maximize | snap_left | snap_right | "
                    "switch_window | show_desktop | task_manager | focus_search | "
                    "refresh_page | close_tab | new_tab | next_tab | prev_tab | "
                    "go_back | go_forward | zoom_in | zoom_out | zoom_reset | "
                    "find_on_page | scroll_up | scroll_down | scroll_top | "
                    "scroll_bottom | page_up | page_down | copy | paste | cut | "
                    "undo | redo | select_all | save | enter | escape | press_key | "
                    "type_text | screenshot | lock_screen | open_settings | "
                    "file_explorer | open_run | dark_mode | toggle_wifi | "
                    "restart | shutdown"
                )
            },
            "description": {
                "type": "STRING",
                "description": (
                    "Fallback only, when no action name above fits. "
                    "Resolved locally — no extra model call."
                )
            },
            "value": {
                "type": "STRING",
                "description": "Optional value: volume level 0-100, text to type, key name, etc."
            }
        },
        "required": []
    },
    "handler": computer_settings,
}
