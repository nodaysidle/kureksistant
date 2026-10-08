"""
actions/screen_vision.py — Screen vision and continuous observation tool for Kurek.
Auto-discovered by core/action_loader.py.

Capabilities:
  • 'inspect': Single snapshot on demand (e.g. "what is on my screen?", "check this error", "what code is this?").
  • 'start_watch': Continuous background monitor (e.g. "watch the screen till I say so and tell me what you think about X").
  • 'stop_watch': Terminates the background watcher.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from PIL import Image

_WATCH_THREAD: threading.Thread | None = None
_WATCH_RUNNING = False
_LAST_IMAGE_HASH: int | None = None
_WATCH_LOCK = threading.Lock()


def _get_gemini_api_key() -> str:
    from memory.config_manager import get_gemini_key, load_api_keys
    key = get_gemini_key()
    if key:
        return key
    keys = load_api_keys()
    key = keys.get("gemini_api_key") or keys.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    # Direct fallback to .env file
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("GEMINI_API_KEY="):
                        return line.split("=", 1)[1].strip().strip("\"'")
        except Exception:
            pass
    return ""


def get_hyprland_context() -> dict:
    """Retrieves active window, class, title, and workspace from Hyprland."""
    if shutil.which("hyprctl"):
        try:
            res = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=1.5)
            if res.returncode == 0 and res.stdout.strip():
                return json.loads(res.stdout)
        except Exception:
            pass
    return {}


def capture_screen_jpeg(max_width: int = 1440) -> tuple[bytes, int]:
    """
    Captures the primary monitor using native OS tools (grim on Wayland, screencapture on macOS, or mss).
    Returns (jpeg_bytes, structural_diff_hash).
    """
    tmp_path = Path("/tmp/kurek_screen_cap.png")

    if sys.platform == "darwin":
        subprocess.run(["screencapture", "-x", str(tmp_path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif shutil.which("grim"):
        subprocess.run(["grim", str(tmp_path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            shot = sct.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            img.save(tmp_path)

    with Image.open(tmp_path) as img:
        img = img.convert("RGB")
        img.thumbnail((max_width, 810), Image.Resampling.BILINEAR)

        # Structural hash for change detection (16x16 thumbnail grayscale sum)
        small = img.resize((16, 16)).convert("L")
        img_hash = sum(small.getdata())

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        jpeg_bytes = buf.getvalue()

    tmp_path.unlink(missing_ok=True)
    return jpeg_bytes, img_hash


def query_vision(jpeg_bytes: bytes, prompt: str) -> str:
    """Sends screen capture + Hyprland context to Google Gemini Vision with model fallback."""
    api_key = _get_gemini_api_key()
    if not api_key:
        return "No GEMINI_API_KEY found in environment or config. Please set it in .env."

    try:
        from google import genai
        from google.genai import types as gtypes
    except ImportError:
        return "google-genai library not installed in Python environment."

    client = genai.Client(api_key=api_key)

    # Gather active Hyprland desktop context
    hypr_ctx = get_hyprland_context()
    context_prefix = ""
    if hypr_ctx and hypr_ctx.get("class"):
        app_cls = hypr_ctx.get("class", "")
        app_title = hypr_ctx.get("title", "")
        ws = hypr_ctx.get("workspace", {}).get("name", "")
        context_prefix = f"[Hyprland Active Window: App '{app_cls}', Title '{app_title}', Workspace '{ws}']\n"

    system_instruction = (
        "You are Kurek's high-speed visual cortex for an Arch Linux (Hyprland) workspace. "
        "You are looking directly at the user's active monitor. "
        "Be candid, sharp, concise, and direct like Jarvis. Answer in 1 to 3 punchy sentences. "
        "Do NOT output markdown asterisks, raw URLs, code blocks, or conversational filler. "
        "Focus on key compiler errors, active windows, code context, or design elements requested."
    )

    models_to_try = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]
    last_err = None

    for model_name in models_to_try:
        try:
            res = client.models.generate_content(
                model=model_name,
                contents=[
                    gtypes.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
                    f"{system_instruction}\n\n{context_prefix}Task/Question: {prompt}"
                ]
            )
            if res.text and res.text.strip():
                clean_text = res.text.strip()
                import re
                clean_text = re.sub(r"\*\*([^*]+)\*\*", r"\1", clean_text)
                clean_text = clean_text.replace("*", "").replace("`", "")
                return clean_text
        except Exception as e:
            last_err = e
            continue

    return f"Vision analysis encountered an error: {last_err}"


def _watch_loop(topic: str, interval: int = 4):
    global _WATCH_RUNNING, _LAST_IMAGE_HASH
    from core.tts import create_tts_player
    from memory.config_manager import get_xai_key
    from core.llm_client import query_deepseek

    print(f"[Kurek Vision Watcher] Started observing topic: '{topic}'", flush=True)
    xai_key = get_xai_key()
    tts = create_tts_player({"tts_engine": "xai", "tts_voice": "sal", "xai_api_key": xai_key})

    tts.speak(f"Watching your screen now for {topic}. I will chime in when I notice something.")

    while _WATCH_RUNNING:
        try:
            jpeg_bytes, current_hash = capture_screen_jpeg()

            # If screen content visibly shifted by threshold
            diff = abs(current_hash - (_LAST_IMAGE_HASH or 0))
            if _LAST_IMAGE_HASH is None or diff > 750:
                _LAST_IMAGE_HASH = current_hash
                print(f"[Kurek Vision Watcher] Screen changed (diff={diff}). Analyzing...", flush=True)

                raw_vision = query_vision(
                    jpeg_bytes,
                    f"The user asked: 'Watch my screen and tell me what you think about {topic}'. "
                    "Analyze the current activity, design, errors, or progress. Keep it direct."
                )

                # Query DeepSeek-Flash to decide if an audible comment is warranted
                prompt = (
                    f"You are Kurek. You are silently watching the user's screen observing: '{topic}'.\n"
                    f"Visual observation just captured: \"{raw_vision}\"\n"
                    "If there is a valuable insight, critique, suggestion, warning, or candid reaction, "
                    "provide a short, punchy 1-2 sentence spoken comment (no markdown, no asterisks). "
                    "If nothing important, actionable, or noteworthy changed, respond with exactly: SILENT"
                )
                comment = query_deepseek(prompt=prompt, model="deepseek-flash")

                if isinstance(comment, str) and "SILENT" not in comment and len(comment.strip()) > 5:
                    print(f"[Kurek Vision Watcher] Speaking observation: {comment}", flush=True)
                    tts.speak(comment.strip())

        except Exception as e:
            print(f"[Kurek Vision Watcher] Loop notice: {e}", flush=True)

        for _ in range(max(1, interval * 2)):
            if not _WATCH_RUNNING:
                break
            time.sleep(0.5)

    print("[Kurek Vision Watcher] Observation stopped.", flush=True)
    tts.speak("I've stopped watching the screen.")


def screen_vision(parameters: dict, player=None, session_memory=None) -> str:
    """Main tool handler invoked by Kurek."""
    global _WATCH_THREAD, _WATCH_RUNNING
    params = parameters or {}
    action = params.get("action", "inspect").lower().strip()
    topic = params.get("topic") or params.get("query") or "what is currently visible on the screen"

    if action in ("inspect", "look", "snapshot", "read"):
        try:
            jpeg_bytes, _ = capture_screen_jpeg()
            return query_vision(jpeg_bytes, topic)
        except Exception as e:
            return f"Failed to capture or analyze screen: {e}"

    if action in ("start_watch", "watch", "start"):
        with _WATCH_LOCK:
            if _WATCH_RUNNING:
                return "I am already watching your screen."
            _WATCH_RUNNING = True
            _WATCH_THREAD = threading.Thread(target=_watch_loop, args=(topic,), daemon=True)
            _WATCH_THREAD.start()
        return f"Understood. I am watching your screen regarding: {topic}. Say 'stop watching' whenever you're done."

    if action in ("stop_watch", "stop", "cancel"):
        with _WATCH_LOCK:
            if not _WATCH_RUNNING:
                return "I was not actively watching your screen."
            _WATCH_RUNNING = False
        return "Stopped watching your screen."

    if action == "status":
        status = "active" if _WATCH_RUNNING else "inactive"
        return f"Screen observation status: {status}"

    return f"Unknown vision action: '{action}'. Available: inspect, start_watch, stop_watch, status."


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "screen_vision",
    "description": (
        "Visual perception and monitor inspection tool. "
        "Use 'inspect' when asked what is on screen, to read text, diagnose errors, or review code. "
        "Use 'start_watch' when asked to continuously watch the screen till told to stop (e.g. 'watch the screen and tell me what you think'). "
        "Use 'stop_watch' when asked to stop watching the screen."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "inspect | start_watch | stop_watch | status"
            },
            "topic": {
                "type": "STRING",
                "description": "What to look for, inspect, or critique on the monitor (e.g. 'UI layout', 'compiler errors', 'active app')."
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": screen_vision
}
