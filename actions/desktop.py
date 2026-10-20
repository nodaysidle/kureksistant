#desktop.py
import os
import sys
import shutil
import subprocess
import tempfile
import platform
from pathlib import Path
from datetime import datetime

try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

_OS = platform.system()  # "Darwin" | "Linux"


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_api_key() -> str:
    from memory.config_manager import get_gemini_key
    return get_gemini_key()

def _get_desktop() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DESKTOP_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Desktop"

def _build_sandbox() -> dict:
    import time

    safe_builtins = {
        "print": print,
        "len": len, "str": str, "int": int, "float": float,
        "bool": bool, "list": list, "dict": dict, "tuple": tuple,
        "range": range, "enumerate": enumerate, "sorted": sorted,
        "isinstance": isinstance, "hasattr": hasattr, "getattr": getattr,
        "max": max, "min": min, "sum": sum, "abs": abs,
        "zip": zip, "map": map, "filter": filter,
    }

    sandbox = {
        "__builtins__": safe_builtins,
        "Path": Path,
        "time": time,
        "shutil": type("shutil", (), {
            "copy2":      shutil.copy2,
            "copytree":   shutil.copytree,
            "disk_usage": shutil.disk_usage,
        })(),
        "os_path": os.path,
    }

    if _PYAUTOGUI:
        sandbox["pyautogui"] = pyautogui

    return sandbox


def _execute_generated_code(code: str, player=None) -> str:
    if not code or code.strip() == "UNSAFE":
        return "This action cannot be performed safely."

    # Kod temizleme
    if code.startswith("```"):
        lines = code.split("\n")
        code  = "\n".join(lines[1:-1]).strip()

    sandbox      = _build_sandbox()
    output_lines = []
    sandbox["__builtins__"]["print"] = lambda *a: output_lines.append(" ".join(str(x) for x in a))

    try:
        exec(compile(code, "<jarvis_desktop>", "exec"), sandbox)
        return "\n".join(output_lines) if output_lines else "Done."
    except Exception as e:
        print(f"[Desktop] Exec error: {e}\nCode:\n{code[:300]}")
        return f"Execution error: {e}"


def _ask_gemini_for_desktop_action(task: str) -> str:

    from google import genai as _genai
    _client = _genai.Client(api_key=_get_api_key(), http_options={"timeout": 60_000})

    desktop = str(_get_desktop())

    os_specific = ""
    if _OS == "Darwin":
        os_specific = "- subprocess is NOT available; use pyautogui or Path only"
    else:
        os_specific = "- subprocess is NOT available; use pyautogui or Path only"

    prompt = f"""You are a desktop automation assistant.
Current OS: {_OS}
Desktop path: {desktop}

Generate safe Python code to accomplish the task below.
Allowed modules ONLY:
- pyautogui (mouse, keyboard — if needed)
- pathlib.Path (file/folder inspection only, no deletion)
- shutil.copy2, shutil.copytree, shutil.disk_usage (NO move, NO rmtree)
- os_path (os.path equivalent, read-only)
- time.sleep
{os_specific}

Hard rules:
- NO file deletion (no unlink, no rmtree, no remove)
- NO subprocess calls
- NO exec() or eval() inside the code
- NO import statements (modules are pre-injected)
- NO file write operations except explicitly requested
- If task cannot be done safely with these tools, output exactly: UNSAFE

Output ONLY the Python code. No explanation, no markdown, no backticks.

Task: {task}"""

    try:
        response = _client.models.generate_content(model="gemini-flash-latest", contents=prompt)
        code = response.text.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            code  = "\n".join(lines[1:-1]).strip()
        return code
    except Exception as e:
        return f"ERROR: {e}"

def _is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))


_OMARCHY_BG_LINK = Path.home() / ".local" / "state" / "omarchy" / "current" / "background"


def _set_wallpaper_wayland(path: Path) -> str:
    """Hyprland/Wayland: Omarchy's own command first, then hyprpaper, then swaybg."""
    errors = []
    if shutil.which("omarchy-theme-bg-set"):
        res = subprocess.run(["omarchy-theme-bg-set", str(path)],
                             capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            return f"Wallpaper set: {path.name}"
        errors.append(f"omarchy-theme-bg-set: {(res.stderr or res.stdout).strip() or res.returncode}")

    if shutil.which("hyprctl") and shutil.which("hyprpaper"):
        pre = subprocess.run(["hyprctl", "hyprpaper", "preload", str(path)],
                             capture_output=True, text=True, timeout=10)
        wal = subprocess.run(["hyprctl", "hyprpaper", "wallpaper", f",{path}"],
                             capture_output=True, text=True, timeout=10)
        if pre.returncode == 0 and wal.returncode == 0 and "ok" in (wal.stdout or "").lower():
            return f"Wallpaper set: {path.name}"
        errors.append(f"hyprpaper: {(wal.stderr or wal.stdout).strip() or wal.returncode}")

    if shutil.which("swaybg"):
        subprocess.run(["pkill", "-x", "swaybg"], capture_output=True, timeout=5)
        subprocess.Popen(["swaybg", "-i", str(path), "-m", "fill"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return f"Wallpaper set: {path.name}"

    if errors:
        return "Could not set wallpaper: " + "; ".join(errors)
    return ("Could not set wallpaper: no Wayland wallpaper tool found "
            "(need omarchy-theme-bg-set, hyprpaper or swaybg).")


def set_wallpaper(image_path: str) -> str:
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        return f"Image not found: {image_path}"
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
        return f"Unsupported format: {path.suffix}. Use jpg, png, bmp or webp."

    try:
        if _OS == "Darwin":
            script = (
                f'tell application "System Events" to tell every desktop to '
                f'set picture to POSIX file "{path}"'
            )
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=15)
            return f"Wallpaper set: {path.name}"

        else:
            desktop_env = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
            uri = f"file://{path}"

            if "gnome" in desktop_env or "unity" in desktop_env:
                subprocess.run([
                    "gsettings", "set", "org.gnome.desktop.background",
                    "picture-uri", uri
                ], capture_output=True, timeout=15)
                subprocess.run([
                    "gsettings", "set", "org.gnome.desktop.background",
                    "picture-uri-dark", uri
                ], capture_output=True, timeout=15)

            elif "kde" in desktop_env:
                # KDE Plasma
                script = f"""
var allDesktops = desktops();
for (var i = 0; i < allDesktops.length; i++) {{
    d = allDesktops[i];
    d.wallpaperPlugin = "org.kde.image";
    d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
    d.writeConfig("Image", "file://{path}");
}}
"""
                subprocess.run(
                    ["qdbus", "org.kde.plasmashell", "/PlasmaShell",
                     "org.kde.PlasmaShell.evaluateScript", script],
                    capture_output=True, timeout=15
                )

            elif "xfce" in desktop_env:
                subprocess.run([
                    "xfconf-query", "-c", "xfce4-desktop",
                    "-p", "/backdrop/screen0/monitor0/workspace0/last-image",
                    "-s", str(path)
                ], capture_output=True, timeout=15)

            elif _is_wayland():
                return _set_wallpaper_wayland(path)

            else:
                if not shutil.which("feh"):
                    return (f"Could not set wallpaper automatically on {desktop_env or 'this desktop'}: "
                            "'feh' is not installed.")
                result = subprocess.run(
                    ["feh", "--bg-scale", str(path)],
                    capture_output=True, timeout=15
                )
                if result.returncode != 0:
                    return (
                        f"Could not set wallpaper automatically on {desktop_env}. "
                        f"Try manually or install 'feh'."
                    )

            return f"Wallpaper set: {path.name}"

    except Exception as e:
        return f"Could not set wallpaper: {e}"


def set_wallpaper_from_url(url: str) -> str:
    try:
        import urllib.request
        suffix = Path(url.split("?")[0]).suffix or ".jpg"
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = resp.read()
        if _OS == "Linux" and _is_wayland():
            # Wayland setters (Omarchy symlink, hyprpaper, swaybg) keep reading
            # the file after we return, so it must outlive this call.
            dest_dir = Path.home() / "Pictures" / "Wallpapers"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"kurek_{datetime.now():%Y%m%d_%H%M%S}{suffix}"
            dest.write_bytes(data)
            return set_wallpaper(str(dest))
        fd, tmp_name = tempfile.mkstemp(suffix=suffix)
        tmp = Path(tmp_name)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        result = set_wallpaper(str(tmp))
        try:
            tmp.unlink()
        except Exception:
            pass
        return result
    except Exception as e:
        return f"Could not download wallpaper: {e}"


def get_current_wallpaper() -> str:
    try:
        if _OS == "Darwin":
            script = (
                'tell application "System Events" to get picture of desktop 1'
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=15
            )
            return f"Current wallpaper: {result.stdout.strip()}"

        else:
            desktop_env = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
            if "gnome" in desktop_env or "unity" in desktop_env:
                result = subprocess.run(
                    ["gsettings", "get", "org.gnome.desktop.background", "picture-uri"],
                    capture_output=True, text=True, timeout=15
                )
                return f"Current wallpaper: {result.stdout.strip()}"
            if _OMARCHY_BG_LINK.exists():
                return f"Current wallpaper: {_OMARCHY_BG_LINK.resolve()}"
            return "Wallpaper path retrieval not supported for this desktop environment."

    except Exception as e:
        return f"Could not get wallpaper: {e}"

FILE_TYPE_MAP = {
    "Images":      {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"},
    "Documents":   {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx",
                    ".ppt", ".pptx", ".csv", ".odt", ".ods", ".odp"},
    "Videos":      {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
    "Music":       {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
    "Archives":    {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Code":        {".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
                    ".cpp", ".java", ".cs", ".go", ".rs", ".sh", ".php"},
    "Executables": {".exe", ".msi", ".bat", ".cmd", ".sh", ".appimage", ".deb", ".rpm"},
}

_SKIP_EXTENSIONS = {
    "Darwin":  {".webloc"},
    "Linux":   {".desktop"},
}


def organize_desktop(mode: str = "by_type") -> str:
    desktop       = _get_desktop()
    skip_exts     = _SKIP_EXTENSIONS.get(_OS, set())
    moved, skipped = [], []

    for item in desktop.iterdir():
        if item.is_dir() or item.name.startswith("."):
            continue
        if item.suffix.lower() in skip_exts:
            continue

        if mode == "by_date":
            mtime       = datetime.fromtimestamp(item.stat().st_mtime)
            folder_name = mtime.strftime("%Y-%m")
        else:
            ext         = item.suffix.lower()
            folder_name = "Others"
            for folder, exts in FILE_TYPE_MAP.items():
                if ext in exts:
                    folder_name = folder
                    break

        target_dir = desktop / folder_name
        target_dir.mkdir(exist_ok=True)
        new_path = target_dir / item.name

        if new_path.exists():
            skipped.append(item.name)
            continue

        shutil.move(str(item), str(new_path))
        moved.append(f"{item.name} → {folder_name}/")

    result = f"Desktop organized ({mode}): {len(moved)} files moved."
    if moved:
        result += "\n" + "\n".join(moved[:8])
        if len(moved) > 8:
            result += f"\n... and {len(moved) - 8} more."
    if skipped:
        result += f"\n{len(skipped)} file(s) skipped (name conflict)."
    return result


def list_desktop() -> str:
    desktop = _get_desktop()
    items   = []
    for item in sorted(desktop.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_dir():
            try:
                count = len(list(item.iterdir()))
            except PermissionError:
                count = "?"
            items.append(f"📁 {item.name}/ ({count} items)")
        else:
            size     = item.stat().st_size
            size_str = (
                f"{size / 1024:.1f} KB" if size < 1024 * 1024
                else f"{size / 1024 / 1024:.1f} MB"
            )
            items.append(f"📄 {item.name} ({size_str})")

    if not items:
        return "Desktop is empty."
    return f"Desktop ({len(items)} items):\n" + "\n".join(items)


def clean_desktop() -> str:
    desktop     = _get_desktop()
    skip_exts   = _SKIP_EXTENSIONS.get(_OS, set())
    today       = datetime.now().strftime("%Y-%m-%d")
    archive_dir = desktop / f"Desktop Archive {today}"
    archive_dir.mkdir(exist_ok=True)

    moved = 0
    for item in desktop.iterdir():
        if item.is_dir() or item.name.startswith("."):
            continue
        if item.suffix.lower() in skip_exts:
            continue
        new_path = archive_dir / item.name
        if not new_path.exists():
            shutil.move(str(item), str(new_path))
            moved += 1

    return f"Desktop cleaned: {moved} files archived to '{archive_dir.name}'."


def get_desktop_stats() -> str:
    desktop    = _get_desktop()
    files      = [i for i in desktop.iterdir() if i.is_file()]
    folders    = [i for i in desktop.iterdir() if i.is_dir()]
    total_size = sum(f.stat().st_size for f in files if f.exists())
    size_str   = (
        f"{total_size / 1024:.1f} KB" if total_size < 1024 * 1024
        else f"{total_size / 1024 / 1024:.1f} MB"
    )
    return (
        f"Desktop stats ({_OS}):\n"
        f"  Files   : {len(files)}\n"
        f"  Folders : {len(folders)}\n"
        f"  Size    : {size_str}\n"
        f"  Path    : {desktop}"
    )

def desktop_control(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    parameters:
        action : wallpaper | wallpaper_url | current_wallpaper |
                 organize  | clean | list | stats |
                 task (AI-powered)
        path   : image path for 'wallpaper'
        url    : image URL for 'wallpaper_url'
        mode   : 'by_type' or 'by_date' for 'organize'
        task   : natural language description for AI-powered actions
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    task   = params.get("task", "").strip()

    if player:
        player.write_log(f"[desktop] {action or task[:40]}")

    try:
        if action == "wallpaper":
            path = params.get("path", "")
            return set_wallpaper(path) if path else "No image path provided."

        elif action == "wallpaper_url":
            url = params.get("url", "")
            return set_wallpaper_from_url(url) if url else "No URL provided."

        elif action == "current_wallpaper":
            return get_current_wallpaper()

        elif action == "organize":
            return organize_desktop(params.get("mode", "by_type"))

        elif action == "clean":
            return clean_desktop()

        elif action == "list":
            return list_desktop()

        elif action == "stats":
            return get_desktop_stats()

        elif action == "task" or task:
            actual_task = task or params.get("description", "")
            if not actual_task:
                return "Please describe what you want to do on the desktop."

            print(f"[Desktop] Asking Gemini: {actual_task}")
            if player:
                player.write_log("[Desktop] Generating action...")

            code = _ask_gemini_for_desktop_action(actual_task)
            return _execute_generated_code(code, player=player)

        else:
            if action:
                code = _ask_gemini_for_desktop_action(action)
                return _execute_generated_code(code, player=player)
            return "No action or task specified."

    except Exception as e:
        print(f"[Desktop] Error: {e}")
        return f"Desktop control error: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "desktop_control",
    "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"
            },
            "path": {
                "type": "STRING",
                "description": "Image path for wallpaper"
            },
            "url": {
                "type": "STRING",
                "description": "Image URL for wallpaper_url"
            },
            "mode": {
                "type": "STRING",
                "description": "by_type or by_date for organize"
            },
            "task": {
                "type": "STRING",
                "description": "Natural language desktop task"
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": desktop_control,
}
