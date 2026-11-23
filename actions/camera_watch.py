"""
actions/camera_watch.py — Entrance camera inspection & alert control for Kurek.
Auto-discovered by core/action_loader.py.

Capabilities:
  • 'check': Grabs a live frame from the configured RTSP camera (Okno Vhod) and
             describes what is currently outside (vehicles, people, activity) using
             local YOLO vehicle detection plus Gemini Vision.
  • 'mute': Mutes entrance vehicle notifications for N minutes (default 60).
  • 'unmute': Re-enables entrance vehicle notifications immediately.
  • 'status': Reports watcher state, mute timer, and last vehicle alert.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from core.camera_watcher import (
    capture_rtsp_frame,
    detect_vehicles,
    get_camera_watcher,
    load_camera_config,
)


def _describe_frame_with_vision(jpeg_bytes: bytes, question: str) -> str:
    """Uses Gemini Vision (reusing screen_vision's key loader) on a camera frame."""
    from actions.screen_vision import _get_gemini_api_key

    api_key = _get_gemini_api_key()
    if not api_key:
        return ""
    try:
        from google import genai
        from google.genai import types

        with Image.open(io.BytesIO(jpeg_bytes)) as img:
            rgb = img.convert("RGB")
            rgb.thumbnail((1280, 720), Image.Resampling.BILINEAR)
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=82)
            resized_bytes = buf.getvalue()

        client = genai.Client(api_key=api_key)
        prompt = (
            question
            or "Look through the window at the courtyard/entrance outside. "
            "Briefly state whether any car, vehicle, or person is currently in the "
            "entrance area (ignore the parked car far on the right edge and any indoor "
            "objects or cats unless asked). Keep it to 1-2 short natural sentences."
        )
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=resized_bytes, mime_type="image/jpeg"),
                prompt,
            ],
        )
        return (resp.text or "").strip()
    except Exception as exc:
        print(f"[CameraWatch] Vision error: {exc}", flush=True)
        return ""


def camera_watch(parameters: dict[str, Any] | None = None, **_: Any) -> str:
    params = parameters or {}
    action = str(params.get("action", "check")).strip().lower()
    watcher = get_camera_watcher()
    cfg = watcher.config or load_camera_config()

    if action == "mute":
        minutes = float(params.get("minutes", 60) or 60)
        watcher.mute(minutes)
        mins_int = int(round(minutes))
        cam_name = cfg.name if cfg else "entrance camera"
        return f"Muted {cam_name} alerts for {mins_int} minutes."

    if action == "unmute":
        watcher.unmute()
        cam_name = cfg.name if cfg else "entrance camera"
        return f"Unmuted {cam_name} alerts."

    if action == "test":
        frame = capture_rtsp_frame(cfg.rtsp_url, timeout=6.0) if (cfg and cfg.rtsp_url) else None
        alert = watcher.trigger_test_alert(jpeg_bytes=frame)
        return f"Triggered test alert #{alert['id']} for {alert['camera']} (PC + phone)."

    if action == "status":
        st = watcher.status()
        if not st["configured"]:
            return "Entrance camera is not configured."
        state = "running" if st["running"] else "stopped"
        mute_note = (
            f"muted for {st['muted_seconds_remaining'] // 60}m"
            if st["muted"]
            else "alerts active"
        )
        last = st["last_alert"]
        last_note = (
            f"Last alert: {last['message']} at {last['timestamp']}."
            if last
            else "No vehicle alerts since startup."
        )
        return f"{st['camera']} watcher is {state} ({mute_note}). {last_note}"

    # Default: 'check' live entrance camera
    if not cfg or not cfg.rtsp_url:
        return "Entrance camera is not configured in ~/.config/kurek/camera.json."

    frame = capture_rtsp_frame(cfg.rtsp_url, timeout=6.0)
    if frame is None:
        return f"Could not reach {cfg.name} camera right now."

    # Save latest frame to ~/Downloads/KUREK/doorcam/ and push snapshot to phone Pictures/doorcam/
    question = str(params.get("question", "")).strip()
    vision_desc = _describe_frame_with_vision(frame, question)
    if vision_desc:
        watcher.trigger_test_alert(jpeg_bytes=frame, message=vision_desc, speak=False)
        return vision_desc

    # Local YOLO fallback if Gemini Vision is unavailable
    sess = watcher._ensure_session()
    if sess is not None:
        hits = detect_vehicles(frame, sess, roi=cfg.roi, conf_thresh=cfg.confidence)
        if hits:
            labels = ", ".join(f"{h['label']} ({int(h['confidence']*100)}%)" for h in hits)
            summary = f"Detected at {cfg.name}: {labels}."
        else:
            summary = f"No vehicles currently detected in the {cfg.name} courtyard."
        watcher.trigger_test_alert(jpeg_bytes=frame, message=summary, speak=False)
        return summary
    watcher.trigger_test_alert(
        jpeg_bytes=frame, message=f"Snapshot captured from {cfg.name}.", speak=False
    )
    return f"Captured frame from {cfg.name}, but vision analysis is unavailable."


TOOL = {
    "name": "camera_watch",
    "description": (
        "Check the house entrance camera (Okno Vhod) live, mute/unmute car arrival "
        "notifications, or check camera watcher status. Use when the user asks "
        "'check the entrance camera', 'is there a car outside?', 'who is at the door?', "
        "'mute entrance alerts for an hour', or 'unmute camera alerts'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["check", "mute", "unmute", "status", "test"],
                "description": "check (live snapshot + description), mute, unmute, status, or test (send test alert to PC + phone).",
            },
            "minutes": {
                "type": "NUMBER",
                "description": "Minutes to mute alerts (for action='mute', default 60).",
            },
            "question": {
                "type": "STRING",
                "description": "Optional specific question about what is visible on the entrance camera.",
            },
        },
        "required": ["action"],
    },
    "handler": camera_watch,
}
