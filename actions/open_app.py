import os
import re
import json
import shlex
import time
import subprocess
import platform
import shutil
from pathlib import Path

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_SYSTEM = platform.system()

_APP_ALIASES: dict[str, dict[str, str]] = {
    "grok":               {"Windows": "grok",                    "Darwin": "Grok",                 "Linux": "grok-bot"},
    "grokbot":            {"Windows": "grok",                    "Darwin": "Grok",                 "Linux": "grok-bot"},
    "grok bot":           {"Windows": "grok",                    "Darwin": "Grok",                 "Linux": "grok-bot"},
    "grok-bot":           {"Windows": "grok",                    "Darwin": "Grok",                 "Linux": "grok-bot"},

    "chrome":             {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                 "Darwin": "Firefox",              "Linux": "firefox"},
    "edge":               {"Windows": "msedge",                  "Darwin": "Microsoft Edge",       "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                   "Darwin": "Brave Browser",        "Linux": "brave-browser"},
    "safari":             {"Windows": "msedge",                  "Darwin": "Safari",               "Linux": "firefox"},
    "opera":              {"Windows": "opera",                   "Darwin": "Opera",                "Linux": "opera"},
    "whatsapp":           {"Windows": "WhatsApp",                "Darwin": "WhatsApp",             "Linux": "whatsapp"},
    "telegram":           {"Windows": "Telegram",                "Darwin": "Telegram",             "Linux": "telegram"},
    "discord":            {"Windows": "Discord",                 "Darwin": "Discord",              "Linux": "discord"},
    "slack":              {"Windows": "Slack",                   "Darwin": "Slack",                "Linux": "slack"},
    "zoom":               {"Windows": "Zoom",                    "Darwin": "zoom.us",              "Linux": "zoom"},
    "teams":              {"Windows": "msteams",                 "Darwin": "Microsoft Teams",      "Linux": "teams"},
    "skype":              {"Windows": "skype",                   "Darwin": "Skype",                "Linux": "skype"},
    "signal":             {"Windows": "signal",                  "Darwin": "Signal",               "Linux": "signal"},
    "spotify":            {"Windows": "Spotify",                 "Darwin": "Spotify",              "Linux": "spotify"},
    "vlc":                {"Windows": "vlc",                     "Darwin": "VLC",                  "Linux": "vlc"},
    "netflix":            {"Windows": "Netflix",                 "Darwin": "Netflix",              "Linux": "firefox"},
    "vscode":             {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "visual studio code": {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "code":               {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "ghostty":            {"Windows": "ghostty",                 "Darwin": "Ghostty",              "Linux": "ghostty"},
    "herdr":              {"Windows": "herdr",                   "Darwin": "herdr",                "Linux": "herdr"},
    "fish":               {"Windows": "powershell.exe",          "Darwin": "fish",                 "Linux": "ghostty -e fish"},
    "terminal":           {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "x-terminal-emulator"},
    "cmd":                {"Windows": "cmd.exe",                 "Darwin": "Terminal",             "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",          "Darwin": "Terminal",             "Linux": "bash"},
    "postman":            {"Windows": "Postman",                 "Darwin": "Postman",              "Linux": "postman"},
    "git":                {"Windows": "git-bash",                "Darwin": "Terminal",             "Linux": "bash"},
    "figma":              {"Windows": "Figma",                   "Darwin": "Figma",                "Linux": "figma"},
    "blender":            {"Windows": "blender",                 "Darwin": "Blender",              "Linux": "blender"},
    "word":               {"Windows": "winword",                 "Darwin": "Microsoft Word",       "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                   "Darwin": "Microsoft Excel",      "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",                "Darwin": "Microsoft PowerPoint", "Linux": "libreoffice --impress"},
    "libreoffice":        {"Windows": "soffice",                 "Darwin": "LibreOffice",          "Linux": "libreoffice"},
    "notepad":            {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "textedit":           {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "explorer":           {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "finder":             {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "task manager":       {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",            "Darwin": "System Preferences",   "Linux": "gnome-control-center"},
    "calculator":         {"Windows": "calc.exe",                "Darwin": "Calculator",           "Linux": "gnome-calculator"},
    "paint":              {"Windows": "mspaint.exe",             "Darwin": "Preview",              "Linux": "gimp"},
    "instagram":          {"Windows": "Instagram",               "Darwin": "Instagram",            "Linux": "firefox"},
    "tiktok":             {"Windows": "TikTok",                  "Darwin": "TikTok",               "Linux": "firefox"},
    "notion":             {"Windows": "Notion",                  "Darwin": "Notion",               "Linux": "notion"},
    "obsidian":           {"Windows": "Obsidian",                "Darwin": "Obsidian",             "Linux": "obsidian"},
    "capcut":             {"Windows": "CapCut",                  "Darwin": "CapCut",               "Linux": "capcut"},
    "steam":              {"Windows": "steam",                   "Darwin": "Steam",                "Linux": "steam"},
    "epic":               {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    "epic games":         {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
}


def _clean_alphanumeric(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", s).lower()


def _normalize(raw: str) -> str:
    key = raw.lower().strip()

    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(_SYSTEM, raw)

    clean_key = _clean_alphanumeric(key)
    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key == key or _clean_alphanumeric(alias_key) == clean_key:
            return os_map.get(_SYSTEM, raw)

    for alias_key, os_map in _APP_ALIASES.items():
        clean_alias = _clean_alphanumeric(alias_key)
        if clean_alias in clean_key or clean_key in clean_alias:
            return os_map.get(_SYSTEM, raw)

    return raw

def _launch_windows(app_name: str) -> bool:

    if shutil.which(app_name) or shutil.which(app_name.split(".")[0]):
        try:
            subprocess.Popen(
                app_name,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            return True
        except Exception as e:
            print(f"[open_app] subprocess failed: {e}")

    if ":" in app_name:
        try:
            subprocess.Popen(f"start {app_name}", shell=True)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.7)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.9)
        pyautogui.press("enter")
        time.sleep(2.5)
        return True
    except Exception as e:
        print(f"[open_app] Start Menu search failed: {e}")

    return False


def _launch_macos(app_name: str) -> bool:

    try:
        result = subprocess.run(
            ["open", "-a", app_name],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["open", "-a", f"{app_name}.app"],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    binary = shutil.which(app_name) or shutil.which(app_name.lower())
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] Spotlight failed: {e}")

    return False


_LINUX_TERMINAL_FALLBACKS = [
    "ghostty", "herdr", "alacritty", "kitty", "foot", "wezterm",
    "x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "xterm",
]


def _focus_hyprland_app(app_query: str) -> bool:
    """If app is already open in Hyprland, switch workspace and focus its window."""
    if not shutil.which("hyprctl"):
        return False
    try:
        res = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=2.0)
        if res.returncode != 0 or not res.stdout.strip():
            return False
        clients = json.loads(res.stdout)
        clean_q = _clean_alphanumeric(app_query)
        q_lower = app_query.lower()
        match = None
        for c in clients:
            props = [c.get("class", ""), c.get("title", ""), c.get("initialClass", ""), c.get("initialTitle", "")]
            if any(q_lower in p.lower() for p in props if p):
                match = c
                break
            if any(clean_q in _clean_alphanumeric(p) for p in props if p):
                match = c
                break
        if not match:
            return False

        ws_id = match.get("workspace", {}).get("id")
        if ws_id is not None:
            subprocess.run(["hyprctl", "dispatch", "workspace", str(ws_id)], capture_output=True, timeout=2.0)
        addr = match.get("address")
        if addr:
            subprocess.run(["hyprctl", "dispatch", "focuswindow", f"address:{addr}"], capture_output=True, timeout=2.0)
        return True
    except Exception as e:
        print(f"[open_app] Hyprland focus check failed: {e}")
        return False


def _find_linux_desktop_file(app_name: str) -> Path | None:
    """Find a .desktop file in standard XDG application directories matching app_name."""
    clean_q = _clean_alphanumeric(app_name)
    app_dirs = [
        Path.home() / ".local/share/applications",
        Path("/usr/local/share/applications"),
        Path("/usr/share/applications"),
    ]
    xdg_data = os.environ.get("XDG_DATA_DIRS", "")
    for d in xdg_data.split(":"):
        if d:
            cand = Path(d) / "applications"
            if cand not in app_dirs:
                app_dirs.append(cand)

    for d in app_dirs:
        if not d.exists():
            continue
        for f in d.glob("*.desktop"):
            stem_clean = _clean_alphanumeric(f.stem)
            if clean_q == stem_clean or (len(clean_q) >= 3 and clean_q in stem_clean):
                return f
            try:
                for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith("Name="):
                        val = line.split("=", 1)[1].strip()
                        if clean_q == _clean_alphanumeric(val) or (len(clean_q) >= 3 and clean_q in _clean_alphanumeric(val)):
                            return f
                    elif line.startswith("StartupWMClass="):
                        val = line.split("=", 1)[1].strip()
                        if clean_q == _clean_alphanumeric(val):
                            return f
            except Exception:
                continue
    return None


def _launch_linux(app_name: str) -> bool:
    # 1. If already running on Hyprland, switch workspace and focus
    if _focus_hyprland_app(app_name):
        return True

    # 2. Terminal emulators and shells: try common ones in order
    clean_name = app_name.strip().lower()
    if clean_name in ("terminal", "x-terminal-emulator", "ghostty", "herdr", "shell", "bash", "gnome-terminal"):
        for term in _LINUX_TERMINAL_FALLBACKS:
            if shutil.which(term):
                try:
                    subprocess.Popen(
                        [term],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    time.sleep(1.0)
                    _focus_hyprland_app(term)
                    return True
                except Exception:
                    continue

    if clean_name in ("fish", "ghostty -e fish"):
        for term in _LINUX_TERMINAL_FALLBACKS:
            if shutil.which(term):
                fish_bin = shutil.which("fish") or "fish"
                try:
                    subprocess.Popen(
                        [term, "-e", fish_bin],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    time.sleep(1.0)
                    _focus_hyprland_app(term)
                    return True
                except Exception:
                    continue

    # 3. Direct binary check (including camelCase / kebab-case splits)
    candidates = [
        app_name,
        app_name.lower(),
        app_name.lower().replace(" ", "-"),
        app_name.lower().replace(" ", "_"),
        re.sub(r"([a-z])([A-Z])", r"\1-\2", app_name).lower(),
        re.sub(r"([a-z])([A-Z])", r"\1_\2", app_name).lower(),
    ]
    seen = set()
    binary = None
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        b = shutil.which(cand)
        if b:
            binary = b
            break

    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(1.0)
            _focus_hyprland_app(app_name)
            return True
        except Exception:
            pass

    # 4. Search installed .desktop files
    desktop_file = _find_linux_desktop_file(app_name)
    if desktop_file:
        try:
            res = subprocess.run(
                ["gtk-launch", desktop_file.stem],
                capture_output=True, timeout=5,
            )
            if res.returncode == 0:
                time.sleep(1.0)
                _focus_hyprland_app(app_name)
                return True
        except Exception:
            pass

        try:
            for line in desktop_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("Exec="):
                    exec_cmd = line.split("=", 1)[1].strip()
                    exec_cmd = re.sub(r"%[a-zA-Z]", "", exec_cmd).strip()
                    if exec_cmd:
                        subprocess.Popen(
                            shlex.split(exec_cmd),
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        time.sleep(1.0)
                        _focus_hyprland_app(app_name)
                        return True
        except Exception:
            pass

    # 5. Try gtk-launch with candidate names
    for desktop_name in [
        app_name.lower(),
        app_name.lower().replace(" ", "-"),
        app_name.lower().replace(" ", ""),
        re.sub(r"([a-z])([A-Z])", r"\1-\2", app_name).lower(),
    ]:
        try:
            result = subprocess.run(
                ["gtk-launch", desktop_name],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                time.sleep(1.0)
                _focus_hyprland_app(app_name)
                return True
        except Exception:
            pass

    # 6. Only for URLs or existing files, try xdg-open with returncode verification
    if app_name.startswith(("http://", "https://")) or Path(app_name).exists():
        try:
            res = subprocess.run(
                ["xdg-open", app_name],
                capture_output=True, timeout=5,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass

    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}

def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name:
        return "No application name provided."

    launcher = _OS_LAUNCHERS.get(_SYSTEM)
    if launcher is None:
        return f"Unsupported operating system: {_SYSTEM}"

    normalized = _normalize(app_name)
    print(f"[open_app] Launching: '{app_name}' → '{normalized}' ({_SYSTEM})")

    if player:
        player.write_log(f"[open_app] {app_name}")

    try:
        if launcher(normalized):
            return f"Opened {app_name}."
        if normalized.lower() != app_name.lower():
            if launcher(app_name):
                return f"Opened {app_name}."
        return (
            f"Could not confirm that {app_name} launched. "
            f"It may still be loading, or it might not be installed."
        )
    except Exception as e:
        print(f"[open_app] Error: {e}")
        return f"Failed to open {app_name}: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "open_app",
    "description": "Opens any application on the computer. Use this whenever the user asks to open, launch, or start any app, website, or program. Always call this tool — never just say you opened it.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
            }
        },
        "required": [
            "app_name"
        ]
    },
    "handler": open_app,
}
