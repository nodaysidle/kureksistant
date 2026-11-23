"""
core/camera_watcher.py — Local RTSP vehicle watcher for Kurek.

Pulls periodic single frames via ffmpeg from a local EZVIZ/RTSP camera and runs
a lightweight YOLOv8n ONNX detector (CPU via onnxruntime) restricted to vehicle
classes inside a configurable Region of Interest (ROI), ignoring cats and indoor
motion.

On vehicle arrival (clear -> present transition, confirmed across consecutive
frames), emits:
  1. Desktop notification (notify-send) with the captured JPEG snapshot
  2. Spoken announcement via core.tts.announce (never cuts active speech)
  3. An alert event in the in-memory ring buffer for Kurek Remote (/remote/alerts)
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

VEHICLE_CLASSES: dict[int, str] = {
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "kurek" / "camera.json"
DEFAULT_MODEL_PATH = Path.home() / ".local" / "share" / "kurek" / "models" / "yolov8n.onnx"
DEFAULT_SNAPSHOT_PATH = Path.home() / ".cache" / "kurek" / "camera_latest.jpg"
DEFAULT_DOORCAM_DIR = Path.home() / "Downloads" / "KUREK" / "doorcam"
ICON_PATH = Path.home() / ".local" / "share" / "icons" / "kurek.png"


@dataclass
class CameraConfig:
    enabled: bool = False
    name: str = "Entrance"
    rtsp_url: str = ""
    roi: tuple[float, float, float, float] | None = (0.14, 0.15, 0.72, 0.88)
    poll_seconds: float = 2.0
    confidence: float = 0.45
    confirm_frames: int = 2
    clear_frames: int = 3
    cooldown_seconds: float = 60.0
    speak_on_pc: bool = True
    model_path: str = str(DEFAULT_MODEL_PATH)


def get_camera_config_path() -> Path:
    override = os.environ.get("KUREK_CAMERA_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_CONFIG_PATH


def load_camera_config(path: Path | None = None) -> CameraConfig | None:
    cfg_path = path or get_camera_config_path()
    if not cfg_path.exists():
        return None
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[CameraWatcher] Failed to read {cfg_path}: {exc}", flush=True)
        return None
    if not isinstance(raw, dict):
        return None
    roi_raw = raw.get("roi")
    roi: tuple[float, float, float, float] | None = None
    if isinstance(roi_raw, (list, tuple)) and len(roi_raw) == 4:
        roi = tuple(float(v) for v in roi_raw)  # type: ignore[assignment]
    return CameraConfig(
        enabled=bool(raw.get("enabled", False)),
        name=str(raw.get("name", "Entrance")),
        rtsp_url=str(raw.get("rtsp_url", "")).strip(),
        roi=roi,
        poll_seconds=max(0.5, float(raw.get("poll_seconds", 2.0))),
        confidence=float(raw.get("confidence", 0.45)),
        confirm_frames=max(1, int(raw.get("confirm_frames", 2))),
        clear_frames=max(1, int(raw.get("clear_frames", 3))),
        cooldown_seconds=max(5.0, float(raw.get("cooldown_seconds", 60.0))),
        speak_on_pc=bool(raw.get("speak_on_pc", True)),
        model_path=str(
            Path(str(raw.get("model_path", DEFAULT_MODEL_PATH))).expanduser()
        ),
    )


def capture_rtsp_frame(rtsp_url: str, timeout: float = 6.0) -> bytes | None:
    """Grabs a single JPEG frame from an RTSP stream over TCP using ffmpeg."""
    if not rtsp_url or not shutil.which("ffmpeg"):
        return None
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        rtsp_url,
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-q:v",
        "4",
        "-",
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if res.returncode == 0 and res.stdout.startswith(b"\xff\xd8"):
            return res.stdout
    except Exception:
        return None
    return None


def _crop_roi(
    img: Image.Image, roi: tuple[float, float, float, float] | None
) -> Image.Image:
    if roi is None:
        return img
    x1, y1, x2, y2 = roi
    w, h = img.size
    left = max(0, min(w - 1, int(w * x1)))
    top = max(0, min(h - 1, int(h * y1)))
    right = max(left + 1, min(w, int(w * x2)))
    bottom = max(top + 1, min(h, int(h * y2)))
    return img.crop((left, top, right, bottom))


def _preprocess_yolo(img: Image.Image, size: int = 640) -> np.ndarray:
    orig_w, orig_h = img.size
    scale = min(size / orig_w, size / orig_h)
    nw, nh = max(1, int(round(orig_w * scale))), max(1, int(round(orig_h * scale)))
    resized = img.resize((nw, nh), Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    canvas.paste(resized, ((size - nw) // 2, (size - nh) // 2))
    arr = np.asarray(canvas, dtype=np.float32) / 255.0
    return np.transpose(arr, (2, 0, 1))[None, ...]


def detect_vehicles(
    jpeg_bytes: bytes,
    session: Any,
    roi: tuple[float, float, float, float] | None = None,
    conf_thresh: float = 0.45,
) -> list[dict[str, Any]]:
    """Runs YOLOv8n ONNX on a JPEG frame and returns best hit per vehicle class."""
    with Image.open(io.BytesIO(jpeg_bytes)) as raw_img:
        img = raw_img.convert("RGB")
        cropped = _crop_roi(img, roi)
        inp_tensor = _preprocess_yolo(cropped, size=640)

    input_name = session.get_inputs()[0].name
    preds = session.run(None, {input_name: inp_tensor})[0]
    preds = np.squeeze(preds, 0).T  # (8400, 84)
    scores = preds[:, 4:]
    class_ids = np.argmax(scores, axis=1)
    confs = scores[np.arange(len(preds)), class_ids]
    mask = confs >= conf_thresh

    best_by_label: dict[str, float] = {}
    for cid, cf in zip(class_ids[mask], confs[mask]):
        label = VEHICLE_CLASSES.get(int(cid))
        if label is not None:
            score = float(cf)
            if score > best_by_label.get(label, 0.0):
                best_by_label[label] = score

    return [
        {"label": label, "confidence": round(score, 3)}
        for label, score in sorted(
            best_by_label.items(), key=lambda kv: kv[1], reverse=True
        )
    ]


@dataclass
class _ArrivalState:
    present: bool = False
    hit_streak: int = 0
    clear_streak: int = 0
    last_alert_ts: float = 0.0
    alerts: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=50))
    next_alert_id: int = 1


class CameraWatcher:
    """Background RTSP vehicle watcher with mute control and remote alert feed."""

    def __init__(
        self,
        config: CameraConfig | None = None,
        snapshot_path: Path | None = None,
        doorcam_dir: Path | None = None,
    ) -> None:
        self.config = config or load_camera_config()
        self.snapshot_path = snapshot_path or DEFAULT_SNAPSHOT_PATH
        self.doorcam_dir = doorcam_dir or DEFAULT_DOORCAM_DIR
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._session: Any = None
        self._mute_until: float = 0.0
        self._last_frame_ts: float = 0.0
        self._last_error: str = ""
        self._latest_jpeg: bytes | None = None
        self._state = _ArrivalState()

    def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        if not self.config:
            return None
        model_file = Path(self.config.model_path)
        if not model_file.exists():
            self._last_error = f"ONNX model missing: {model_file}"
            return None
        import onnxruntime as ort

        self._session = ort.InferenceSession(
            str(model_file), providers=["CPUExecutionProvider"]
        )
        return self._session

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            if self.config is None:
                self.config = load_camera_config()
            if not self.config or not self.config.enabled or not self.config.rtsp_url:
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop, name="kurek-camera-watcher", daemon=True
            )
            self._thread.start()
            print(
                f"[CameraWatcher] Started watching '{self.config.name}' "
                f"(poll={self.config.poll_seconds}s, conf={self.config.confidence})",
                flush=True,
            )
            return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def mute(self, minutes: float = 60.0) -> float:
        until = time.time() + max(1.0, float(minutes)) * 60.0
        with self._lock:
            self._mute_until = until
        return until

    def unmute(self) -> None:
        with self._lock:
            self._mute_until = 0.0

    def is_muted(self, now: float | None = None) -> bool:
        ts = time.time() if now is None else now
        with self._lock:
            return ts < self._mute_until

    def status(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            muted_rem = max(0, int(self._mute_until - now))
            last_alert = self._state.alerts[-1] if self._state.alerts else None
            return {
                "configured": self.config is not None and bool(self.config.rtsp_url),
                "running": self.is_running(),
                "camera": self.config.name if self.config else "",
                "muted": muted_rem > 0,
                "muted_seconds_remaining": muted_rem,
                "vehicle_present": self._state.present,
                "last_frame_age_s": (
                    round(now - self._last_frame_ts, 1) if self._last_frame_ts else None
                ),
                "last_alert": last_alert,
                "last_error": self._last_error,
            }

    def get_alerts_since(self, since_id: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [a for a in self._state.alerts if a["id"] > since_id]

    def get_latest_snapshot(self) -> bytes | None:
        with self._lock:
            if self._latest_jpeg:
                return self._latest_jpeg
        if self.snapshot_path.exists():
            try:
                return self.snapshot_path.read_bytes()
            except Exception:
                return None
        return None

    def trigger_test_alert(
        self, jpeg_bytes: bytes | None = None, message: str | None = None, speak: bool = True
    ) -> dict[str, Any]:
        """Emits an alert/snapshot through both PC (doorcam) and remote phone alert paths."""
        ts = time.time()
        cam_name = self.config.name if self.config else "Entrance"
        with self._lock:
            st = self._state
            alert_id = max(st.next_alert_id, int(ts))
            alert = {
                "id": alert_id,
                "timestamp": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
                "epoch": int(ts),
                "camera": cam_name,
                "label": "snapshot" if message else "car",
                "confidence": 1.0,
                "message": message or f"Test alert: Car detected at {cam_name}.",
                "has_snapshot": jpeg_bytes is not None,
            }
            st.next_alert_id = alert_id + 1
            st.alerts.append(alert)
            if jpeg_bytes is not None:
                self._latest_jpeg = jpeg_bytes
        self._emit_local_alert(alert, jpeg_bytes, speak_override=speak)
        return alert

    def process_detections(
        self,
        detections: list[dict[str, Any]],
        jpeg_bytes: bytes | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Pure arrival state machine; returns an alert dict when triggered."""
        cfg = self.config
        if cfg is None:
            return None
        ts = time.time() if now is None else now
        alert: dict[str, Any] | None = None

        with self._lock:
            st = self._state
            if detections:
                st.hit_streak += 1
                st.clear_streak = 0
                if not st.present and st.hit_streak >= cfg.confirm_frames:
                    st.present = True
                    muted = ts < self._mute_until
                    cooled = (ts - st.last_alert_ts) >= cfg.cooldown_seconds
                    if not muted and cooled:
                        st.last_alert_ts = ts
                        top = detections[0]
                        label = top["label"]
                        msg = f"{label.capitalize()} detected at {cfg.name}."
                        alert_id = max(st.next_alert_id, int(ts))
                        alert = {
                            "id": alert_id,
                            "timestamp": datetime.fromtimestamp(ts).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                            "epoch": int(ts),
                            "camera": cfg.name,
                            "label": label,
                            "confidence": top["confidence"],
                            "message": msg,
                            "has_snapshot": jpeg_bytes is not None,
                        }
                        st.next_alert_id = alert_id + 1
                        st.alerts.append(alert)
                        if jpeg_bytes is not None:
                            self._latest_jpeg = jpeg_bytes
            else:
                st.hit_streak = 0
                st.clear_streak += 1
                if st.present and st.clear_streak >= cfg.clear_frames:
                    st.present = False

        if alert is not None:
            self._emit_local_alert(alert, jpeg_bytes)
        return alert

    def _emit_local_alert(
        self,
        alert: dict[str, Any],
        jpeg_bytes: bytes | None,
        speak_override: bool | None = None,
    ) -> None:
        icon_arg = str(ICON_PATH) if ICON_PATH.exists() else "camera-video"
        if jpeg_bytes:
            try:
                self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.snapshot_path.with_suffix(".tmp.jpg")
                tmp.write_bytes(jpeg_bytes)
                tmp.replace(self.snapshot_path)
                icon_arg = str(self.snapshot_path)
            except Exception as exc:
                print(f"[CameraWatcher] Snapshot save error: {exc}", flush=True)
            try:
                self.doorcam_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.fromtimestamp(alert["epoch"]).strftime(
                    "%Y-%m-%d_%H-%M-%S"
                )
                label = str(alert.get("label", "car")).lower()
                archive_path = self.doorcam_dir / f"{stamp}_{label}.jpg"
                archive_path.write_bytes(jpeg_bytes)
            except Exception as exc:
                print(f"[CameraWatcher] Doorcam archive error: {exc}", flush=True)

        title = f"Kurek — {alert['camera']}"
        body = alert["message"]
        if shutil.which("notify-send"):
            try:
                subprocess.run(
                    ["notify-send", "-u", "normal", "-i", icon_arg, title, body],
                    timeout=3,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as exc:
                print(f"[CameraWatcher] notify-send error: {exc}", flush=True)

        should_speak = (
            speak_override
            if speak_override is not None
            else bool(self.config and self.config.speak_on_pc)
        )
        if should_speak:
            try:
                from core.tts import announce

                announce(f"{alert['label'].capitalize()} at {alert['camera']}.")
            except Exception as exc:
                print(f"[CameraWatcher] TTS announce error: {exc}", flush=True)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            cfg = self.config
            if cfg is None or not cfg.rtsp_url:
                self._stop_event.wait(5.0)
                continue
            t0 = time.monotonic()
            try:
                sess = self._ensure_session()
                if sess is None:
                    self._stop_event.wait(10.0)
                    continue
                frame = capture_rtsp_frame(cfg.rtsp_url, timeout=6.0)
                if frame is None:
                    self._last_error = "RTSP frame capture failed"
                    self._stop_event.wait(min(10.0, cfg.poll_seconds * 2))
                    continue
                self._last_frame_ts = time.time()
                self._last_error = ""
                hits = detect_vehicles(
                    frame, sess, roi=cfg.roi, conf_thresh=cfg.confidence
                )
                self.process_detections(hits, jpeg_bytes=frame)
            except Exception as exc:
                self._last_error = str(exc)
                print(f"[CameraWatcher] Loop error: {exc}", flush=True)
            elapsed = time.monotonic() - t0
            wait_s = max(0.2, cfg.poll_seconds - elapsed)
            self._stop_event.wait(wait_s)


_WATCHER_SINGLETON: CameraWatcher | None = None
_SINGLETON_LOCK = threading.Lock()


def get_camera_watcher() -> CameraWatcher:
    global _WATCHER_SINGLETON
    with _SINGLETON_LOCK:
        if _WATCHER_SINGLETON is None:
            _WATCHER_SINGLETON = CameraWatcher()
        return _WATCHER_SINGLETON


def start_camera_watcher_if_configured() -> bool:
    """Starts the singleton watcher only when camera.json exists and enabled=true."""
    watcher = get_camera_watcher()
    return watcher.start()
