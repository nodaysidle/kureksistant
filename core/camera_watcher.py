"""
core/camera_watcher.py — Local RTSP vehicle watcher for Kurek.

Pulls periodic single frames via ffmpeg from a local EZVIZ/RTSP camera and runs
a lightweight YOLOv8n ONNX detector (CPU via onnxruntime) restricted to vehicle
classes inside a configurable Region of Interest (ROI), ignoring cats, indoor
motion, distant neighbor vehicles, and static parked cars.

On vehicle arrival (novel vehicle confirmed across consecutive frames with
motion and geometry validation), emits:
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
from collections.abc import Sequence
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

BBox = tuple[float, float, float, float]


@dataclass
class CameraConfig:
    enabled: bool = False
    name: str = "Entrance"
    rtsp_url: str = ""
    roi: BBox | None = (0.14, 0.15, 0.72, 0.88)
    poll_seconds: float = 2.0
    confidence: float = 0.45
    clear_confidence: float = 0.30
    confirm_frames: int = 2
    clear_frames: int = 3
    cooldown_seconds: float = 60.0
    min_box_area: float = 0.04
    max_box_area: float = 0.90
    min_full_area: float = 0.0
    min_aspect_ratio: float = 0.45
    max_aspect_ratio: float = 3.5
    min_bottom_y: float = 0.0
    exclude_zones: list[BBox] = field(default_factory=list)
    nms_iou: float = 0.45
    stationary_iou: float = 0.40
    stationary_clear_seconds: float = 120.0
    stationary_ttl_seconds: float = 1800.0
    motion_threshold: float = 8.0
    startup_baseline_frames: int = 0
    entry_direction: str = "any"
    min_move_x: float = 0.015
    entry_x_max: float = 0.45
    vision_verify: bool = False
    speak_on_pc: bool = True
    model_path: str = str(DEFAULT_MODEL_PATH)


def get_camera_config_path() -> Path:
    override = os.environ.get("KUREK_CAMERA_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_CONFIG_PATH


def _parse_bbox(raw: Any) -> BBox | None:
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        try:
            return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
        except (TypeError, ValueError):
            return None
    return None


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
    roi = _parse_bbox(raw.get("roi"))
    exclude_zones: list[BBox] = []
    raw_zones = raw.get("exclude_zones")
    if isinstance(raw_zones, list):
        for item in raw_zones:
            parsed = _parse_bbox(item)
            if parsed is not None:
                exclude_zones.append(parsed)

    conf = float(raw.get("confidence", 0.45))
    clear_conf = float(raw.get("clear_confidence", min(0.35, conf)))
    entry_dir = str(raw.get("entry_direction", "any")).strip().lower()
    if entry_dir not in {"any", "left_to_right", "right_to_left"}:
        entry_dir = "any"
    return CameraConfig(
        enabled=bool(raw.get("enabled", False)),
        name=str(raw.get("name", "Entrance")),
        rtsp_url=str(raw.get("rtsp_url", "")).strip(),
        roi=roi,
        poll_seconds=max(0.5, float(raw.get("poll_seconds", 2.0))),
        confidence=conf,
        clear_confidence=max(0.05, min(conf, clear_conf)),
        confirm_frames=max(1, int(raw.get("confirm_frames", 2))),
        clear_frames=max(1, int(raw.get("clear_frames", 3))),
        cooldown_seconds=max(5.0, float(raw.get("cooldown_seconds", 60.0))),
        min_box_area=max(0.0, float(raw.get("min_box_area", 0.04))),
        max_box_area=min(1.0, float(raw.get("max_box_area", 0.90))),
        min_full_area=max(0.0, min(1.0, float(raw.get("min_full_area", 0.0)))),
        min_aspect_ratio=max(0.0, float(raw.get("min_aspect_ratio", 0.45))),
        max_aspect_ratio=max(0.1, float(raw.get("max_aspect_ratio", 3.5))),
        min_bottom_y=max(0.0, min(1.0, float(raw.get("min_bottom_y", 0.0)))),
        exclude_zones=exclude_zones,
        nms_iou=max(0.05, min(0.95, float(raw.get("nms_iou", 0.45)))),
        stationary_iou=max(0.05, min(0.95, float(raw.get("stationary_iou", 0.40)))),
        stationary_clear_seconds=max(
            0.0, float(raw.get("stationary_clear_seconds", 120.0))
        ),
        stationary_ttl_seconds=max(
            0.0, float(raw.get("stationary_ttl_seconds", 1800.0))
        ),
        motion_threshold=max(0.0, float(raw.get("motion_threshold", 8.0))),
        startup_baseline_frames=max(0, int(raw.get("startup_baseline_frames", 0))),
        entry_direction=entry_dir,
        min_move_x=max(0.0, float(raw.get("min_move_x", 0.015))),
        entry_x_max=max(0.0, min(1.0, float(raw.get("entry_x_max", 0.45)))),
        vision_verify=bool(raw.get("vision_verify", False)),
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


def _crop_roi_with_bounds(
    img: Image.Image, roi: BBox | None
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    w, h = img.size
    if roi is None:
        return img, (0, 0, w, h)
    x1, y1, x2, y2 = roi
    left = max(0, min(w - 1, int(w * x1)))
    top = max(0, min(h - 1, int(h * y1)))
    right = max(left + 1, min(w, int(w * x2)))
    bottom = max(top + 1, min(h, int(h * y2)))
    return img.crop((left, top, right, bottom)), (left, top, right, bottom)


def _crop_roi(img: Image.Image, roi: BBox | None) -> Image.Image:
    cropped, _ = _crop_roi_with_bounds(img, roi)
    return cropped


def _preprocess_yolo_with_meta(
    img: Image.Image, size: int = 640
) -> tuple[np.ndarray, float, int, int]:
    orig_w, orig_h = img.size
    scale = min(size / orig_w, size / orig_h)
    nw, nh = max(1, int(round(orig_w * scale))), max(1, int(round(orig_h * scale)))
    pad_x = (size - nw) // 2
    pad_y = (size - nh) // 2
    resized = img.resize((nw, nh), Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    canvas.paste(resized, (pad_x, pad_y))
    arr = np.asarray(canvas, dtype=np.float32) / 255.0
    return np.transpose(arr, (2, 0, 1))[None, ...], scale, pad_x, pad_y


def _preprocess_yolo(img: Image.Image, size: int = 640) -> np.ndarray:
    tensor, _, _, _ = _preprocess_yolo_with_meta(img, size=size)
    return tensor


def _box_iou(a: BBox, b: BBox) -> float:
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def _box_iomin(a: BBox, b: BBox) -> float:
    """Intersection divided by the smaller box's area (handles partial occlusion)."""
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    min_area = min(area_a, area_b)
    return inter / min_area if min_area > 0.0 else 0.0


def _box_area(box: BBox) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _boxes_match(
    cand: BBox, stationary: BBox, iou_thresh: float = 0.40, iomin_thresh: float = 0.75
) -> bool:
    """Matches a candidate detection against a remembered stationary vehicle box.

    Rejects matches when the candidate is significantly larger or closer to the
    camera than the parked vehicle (e.g. a foreground visitor passing in front
    of a parked car). Only uses IoMin containment when the candidate is at most
    1.2x the stationary box's area (handles partial pillar/cat occlusion).
    """
    area_cand = _box_area(cand)
    area_stat = _box_area(stationary)
    if area_stat > 0.0:
        if area_cand > area_stat * 1.6 or (cand[3] - stationary[3]) > 0.10:
            return False
    if _box_iou(cand, stationary) >= iou_thresh:
        return True
    if area_stat > 0.0 and area_cand <= area_stat * 1.2:
        return _box_iomin(cand, stationary) >= iomin_thresh
    return False


def _entry_direction_ok(
    first: BBox | None,
    last: BBox | None,
    direction: str,
    min_move_x: float,
    entry_x_max: float,
) -> bool:
    if direction == "any" or first is None or last is None:
        return True
    first_cx = (first[0] + first[2]) * 0.5
    last_cx = (last[0] + last[2]) * 0.5
    dx = last_cx - first_cx
    if direction == "left_to_right":
        if dx <= -min_move_x:
            return False
        if dx >= min_move_x:
            return True
        return first[0] <= entry_x_max
    if direction == "right_to_left":
        if dx >= min_move_x:
            return False
        if dx <= -min_move_x:
            return True
        return first[2] >= (1.0 - entry_x_max)
    return True


def _verify_arrival_with_vision(jpeg_bytes: bytes, camera_name: str = "Entrance") -> bool:
    """Optional Gemini Flash YES/NO check on candidate alerts; fails open offline."""
    if not jpeg_bytes:
        return True
    try:
        from actions.screen_vision import _get_gemini_api_key

        api_key = _get_gemini_api_key()
        if not api_key:
            return True
        from google import genai
        from google.genai import types

        with Image.open(io.BytesIO(jpeg_bytes)) as img:
            rgb = img.convert("RGB")
            rgb.thumbnail((768, 432), Image.Resampling.BILINEAR)
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=75)
            resized_bytes = buf.getvalue()

        client = genai.Client(api_key=api_key)
        prompt = (
            f"Look at this '{camera_name}' entrance camera frame. "
            "Is there a newly arrived vehicle prominently in the foreground entrance area "
            "(NOT a tiny distant car behind the wall and NOT just a parked gray car in the back bay)? "
            "Reply strictly YES or NO."
        )
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=resized_bytes, mime_type="image/jpeg"),
                prompt,
            ],
        )
        answer = (resp.text or "").strip().upper()
        if answer.startswith("NO"):
            return False
        return True
    except Exception as exc:
        print(f"[CameraWatcher] Vision verify skipped (fail-open): {exc}", flush=True)
        return True


def _point_in_box(x: float, y: float, box: BBox) -> bool:
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _nms_indices(
    boxes: Sequence[BBox], scores: Sequence[float], iou_thresh: float
) -> list[int]:
    order = sorted(range(len(boxes)), key=lambda i: scores[i], reverse=True)
    kept: list[int] = []
    for idx in order:
        box = boxes[idx]
        if all(_box_iou(box, boxes[k]) < iou_thresh for k in kept):
            kept.append(idx)
    return kept


def _frame_to_gray_small(
    jpeg_bytes: bytes, size: tuple[int, int] = (160, 90)
) -> np.ndarray | None:
    try:
        with Image.open(io.BytesIO(jpeg_bytes)) as img:
            gray = img.convert("L").resize(size, Image.Resampling.BILINEAR)
            return np.asarray(gray, dtype=np.float32)
    except Exception:
        return None


def _bbox_motion_diff(
    cur: np.ndarray, ref: np.ndarray, full_bbox: BBox
) -> float:
    h, w = cur.shape
    x1 = max(0, min(w - 1, int(round(full_bbox[0] * w))))
    y1 = max(0, min(h - 1, int(round(full_bbox[1] * h))))
    x2 = max(x1 + 1, min(w, int(round(full_bbox[2] * w))))
    y2 = max(y1 + 1, min(h, int(round(full_bbox[3] * h))))
    patch_cur = cur[y1:y2, x1:x2]
    patch_ref = ref[y1:y2, x1:x2]
    if patch_cur.size == 0:
        return 0.0
    return float(np.mean(np.abs(patch_cur - patch_ref)))


def detect_vehicles(
    jpeg_bytes: bytes,
    session: Any,
    roi: BBox | None = None,
    conf_thresh: float = 0.45,
    *,
    nms_iou: float = 0.45,
    min_box_area: float = 0.04,
    max_box_area: float = 0.90,
    min_full_area: float = 0.0,
    min_aspect_ratio: float = 0.45,
    max_aspect_ratio: float = 3.5,
    min_bottom_y: float = 0.0,
    exclude_zones: Sequence[BBox] | None = None,
) -> list[dict[str, Any]]:
    """Runs YOLOv8n ONNX on a JPEG frame and returns NMS-deduped, geometry-filtered vehicle hits."""
    with Image.open(io.BytesIO(jpeg_bytes)) as raw_img:
        img = raw_img.convert("RGB")
        full_w, full_h = img.size
        cropped, (crop_left, crop_top, _, _) = _crop_roi_with_bounds(img, roi)
        crop_w, crop_h = cropped.size
        inp_tensor, scale, pad_x, pad_y = _preprocess_yolo_with_meta(cropped, size=640)

    input_name = session.get_inputs()[0].name
    preds = session.run(None, {input_name: inp_tensor})[0]
    preds = np.squeeze(preds, 0).T  # (8400, 84)
    scores = preds[:, 4:]
    class_ids = np.argmax(scores, axis=1)
    confs = scores[np.arange(len(preds)), class_ids]
    mask = confs >= conf_thresh

    raw_boxes = preds[mask, :4]
    raw_cids = class_ids[mask]
    raw_confs = confs[mask]

    vehicle_candidates: list[dict[str, Any]] = []
    non_vehicle_boxes: list[tuple[BBox, float]] = []

    for box, cid, cf in zip(raw_boxes, raw_cids, raw_confs):
        cx, cy, bw, bh = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
        rx1 = max(0.0, min(float(crop_w), (cx - bw / 2.0 - pad_x) / scale))
        ry1 = max(0.0, min(float(crop_h), (cy - bh / 2.0 - pad_y) / scale))
        rx2 = max(0.0, min(float(crop_w), (cx + bw / 2.0 - pad_x) / scale))
        ry2 = max(0.0, min(float(crop_h), (cy + bh / 2.0 - pad_y) / scale))
        box_w = rx2 - rx1
        box_h = ry2 - ry1
        if box_w <= 0.0 or box_h <= 0.0:
            continue

        roi_bbox: BBox = (rx1 / crop_w, ry1 / crop_h, rx2 / crop_w, ry2 / crop_h)
        full_bbox: BBox = (
            (crop_left + rx1) / full_w,
            (crop_top + ry1) / full_h,
            (crop_left + rx2) / full_w,
            (crop_top + ry2) / full_h,
        )
        score = float(cf)
        label = VEHICLE_CLASSES.get(int(cid))
        if label is None:
            non_vehicle_boxes.append((full_bbox, score))
            continue

        area = (box_w / crop_w) * (box_h / crop_h)
        full_area = _box_area(full_bbox)
        aspect = box_w / box_h
        if area < min_box_area or area > max_box_area:
            continue
        if full_area < min_full_area:
            continue
        if aspect < min_aspect_ratio or aspect > max_aspect_ratio:
            continue
        if full_bbox[3] < min_bottom_y:
            continue
        if exclude_zones:
            fcx = (full_bbox[0] + full_bbox[2]) * 0.5
            fcy = (full_bbox[1] + full_bbox[3]) * 0.5
            if any(
                _point_in_box(fcx, fcy, zone) and _point_in_box(fcx, full_bbox[3], zone)
                for zone in exclude_zones
            ):
                continue

        vehicle_candidates.append(
            {
                "label": label,
                "confidence": round(score, 3),
                "_score": score,
                "bbox": [round(v, 4) for v in roi_bbox],
                "full_bbox": [round(v, 4) for v in full_bbox],
                "_full_bbox": full_bbox,
                "area": round(area, 4),
                "full_area": round(full_area, 4),
                "aspect": round(aspect, 3),
            }
        )

    if not vehicle_candidates:
        return []

    # Suppress vehicle candidates heavily overlapped by a higher-confidence non-vehicle box (e.g. cat/person)
    if non_vehicle_boxes:
        filtered: list[dict[str, Any]] = []
        for cand in vehicle_candidates:
            cbox = cand["_full_bbox"]
            cscore = cand["_score"]
            conflict = any(
                nv_score > cscore and _box_iou(cbox, nv_box) >= 0.55
                for nv_box, nv_score in non_vehicle_boxes
            )
            if not conflict:
                filtered.append(cand)
        vehicle_candidates = filtered

    if not vehicle_candidates:
        return []

    kept_idx = _nms_indices(
        [c["_full_bbox"] for c in vehicle_candidates],
        [c["_score"] for c in vehicle_candidates],
        iou_thresh=nms_iou,
    )

    results: list[dict[str, Any]] = []
    for i in kept_idx:
        c = vehicle_candidates[i]
        results.append(
            {
                "label": c["label"],
                "confidence": c["confidence"],
                "bbox": c["bbox"],
                "full_bbox": c["full_bbox"],
                "area": c["area"],
                "full_area": c["full_area"],
                "aspect": c["aspect"],
            }
        )
    return results


@dataclass
class _StationaryBox:
    full_bbox: BBox
    last_seen_ts: float
    clear_frames: int = 0


@dataclass
class _ArrivalState:
    present: bool = False
    hit_streak: int = 0
    clear_streak: int = 0
    last_alert_ts: float = 0.0
    alerts: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=50))
    next_alert_id: int = 1
    frames_seen: int = 0
    stationary_boxes: list[_StationaryBox] = field(default_factory=list)
    reference_gray: np.ndarray | None = None
    streak_first_bbox: BBox | None = None
    streak_last_bbox: BBox | None = None


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
            any_visible = self._state.present or any(
                b.clear_frames == 0 for b in self._state.stationary_boxes
            )
            return {
                "configured": self.config is not None and bool(self.config.rtsp_url),
                "running": self.is_running(),
                "camera": self.config.name if self.config else "",
                "muted": muted_rem > 0,
                "muted_seconds_remaining": muted_rem,
                "vehicle_present": any_visible,
                "stationary_count": len(self._state.stationary_boxes),
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

    def detect_frame(self, jpeg_bytes: bytes, session: Any | None = None) -> list[dict[str, Any]]:
        """Runs detect_vehicles with this watcher's full CameraConfig filters and hysteresis floor."""
        cfg = self.config
        if cfg is None:
            return []
        sess = session if session is not None else self._ensure_session()
        if sess is None:
            return []
        floor_conf = min(cfg.confidence, cfg.clear_confidence)
        return detect_vehicles(
            jpeg_bytes,
            sess,
            roi=cfg.roi,
            conf_thresh=floor_conf,
            nms_iou=cfg.nms_iou,
            min_box_area=cfg.min_box_area,
            max_box_area=cfg.max_box_area,
            min_full_area=cfg.min_full_area,
            min_aspect_ratio=cfg.min_aspect_ratio,
            max_aspect_ratio=cfg.max_aspect_ratio,
            min_bottom_y=cfg.min_bottom_y,
            exclude_zones=cfg.exclude_zones,
        )

    def process_frame(
        self,
        jpeg_bytes: bytes,
        session: Any | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """End-to-end single-frame detection + arrival state machine."""
        hits = self.detect_frame(jpeg_bytes, session=session)
        return self.process_detections(hits, jpeg_bytes=jpeg_bytes, now=now)

    def _update_reference_gray(
        self, st: _ArrivalState, gray: np.ndarray | None
    ) -> None:
        if gray is None:
            return
        if st.reference_gray is None or st.reference_gray.shape != gray.shape:
            st.reference_gray = gray.copy()
        else:
            st.reference_gray = 0.75 * st.reference_gray + 0.25 * gray

    def process_detections(
        self,
        detections: list[dict[str, Any]],
        jpeg_bytes: bytes | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Arrival state machine with hysteresis, stationary suppression, and motion gating."""
        cfg = self.config
        if cfg is None:
            return None
        ts = time.time() if now is None else now
        alert: dict[str, Any] | None = None
        cur_gray = (
            _frame_to_gray_small(jpeg_bytes)
            if (jpeg_bytes is not None and cfg.motion_threshold > 0.0)
            else None
        )

        clear_floor = min(cfg.confidence, cfg.clear_confidence)
        present_hits = [
            d for d in detections if float(d.get("confidence", 0.0)) >= clear_floor
        ]
        spatial_hits = [
            (d, _parse_bbox(d.get("full_bbox")))
            for d in present_hits
            if _parse_bbox(d.get("full_bbox")) is not None
        ]

        pending_alert_info: tuple[str, Any, BBox | None] | None = None

        with self._lock:
            st = self._state
            st.frames_seen += 1

            # Startup baseline: seed already-parked vehicles without firing an alert
            if st.frames_seen <= cfg.startup_baseline_frames:
                for _, bbox in spatial_hits:
                    assert bbox is not None
                    matched = False
                    for sbox in st.stationary_boxes:
                        if _boxes_match(bbox, sbox.full_bbox, iou_thresh=cfg.stationary_iou):
                            sbox.last_seen_ts = ts
                            sbox.clear_frames = 0
                            matched = True
                            break
                    if not matched:
                        st.stationary_boxes.append(
                            _StationaryBox(full_bbox=bbox, last_seen_ts=ts, clear_frames=0)
                        )
                self._update_reference_gray(st, cur_gray)
                return None

            # Spatial mode when detections include full_bbox (or stationary boxes are tracked)
            if spatial_hits or st.stationary_boxes:
                matched_stationary: set[int] = set()
                novel_candidates: list[tuple[dict[str, Any], BBox]] = []

                for det, bbox in spatial_hits:
                    assert bbox is not None
                    hit_stationary = False
                    for idx, sbox in enumerate(st.stationary_boxes):
                        if _boxes_match(bbox, sbox.full_bbox, iou_thresh=cfg.stationary_iou):
                            sbox.last_seen_ts = ts
                            sbox.clear_frames = 0
                            matched_stationary.add(idx)
                            hit_stationary = True
                            break
                    if not hit_stationary and float(det.get("confidence", 0.0)) >= cfg.confidence:
                        # Motion gate: if we have a pre-arrival reference frame, require pixel change
                        if (
                            cfg.motion_threshold > 0.0
                            and cur_gray is not None
                            and st.reference_gray is not None
                        ):
                            diff = _bbox_motion_diff(cur_gray, st.reference_gray, bbox)
                            if diff < cfg.motion_threshold:
                                continue
                        novel_candidates.append((det, bbox))

                # Age unmatched stationary boxes; prune only after both clear_frames and clear_seconds (or TTL)
                kept_stationary: list[_StationaryBox] = []
                for idx, sbox in enumerate(st.stationary_boxes):
                    if idx not in matched_stationary:
                        sbox.clear_frames += 1
                    absent_s = ts - sbox.last_seen_ts
                    expired_ttl = cfg.stationary_ttl_seconds > 0.0 and absent_s >= cfg.stationary_ttl_seconds
                    cleared_away = (
                        sbox.clear_frames >= cfg.clear_frames
                        and absent_s >= cfg.stationary_clear_seconds
                    )
                    if not expired_ttl and not cleared_away:
                        kept_stationary.append(sbox)
                st.stationary_boxes = kept_stationary

                if novel_candidates:
                    top_det, top_bbox = novel_candidates[0]
                    if st.hit_streak == 0:
                        st.streak_first_bbox = top_bbox
                    st.streak_last_bbox = top_bbox
                    st.hit_streak += 1
                    st.clear_streak = 0
                    if st.hit_streak >= cfg.confirm_frames:
                        dir_ok = _entry_direction_ok(
                            st.streak_first_bbox,
                            st.streak_last_bbox,
                            cfg.entry_direction,
                            cfg.min_move_x,
                            cfg.entry_x_max,
                        )
                        st.hit_streak = 0
                        st.streak_first_bbox = None
                        st.streak_last_bbox = None
                        if dir_ok:
                            st.stationary_boxes.append(
                                _StationaryBox(full_bbox=top_bbox, last_seen_ts=ts, clear_frames=0)
                            )
                            muted = ts < self._mute_until
                            cooled = (ts - st.last_alert_ts) >= cfg.cooldown_seconds
                            if not muted and cooled:
                                pending_alert_info = (
                                    str(top_det["label"]),
                                    top_det["confidence"],
                                    top_bbox,
                                )
                        # Update reference after confirming/rejecting streak
                        self._update_reference_gray(st, cur_gray)
                else:
                    st.hit_streak = 0
                    st.streak_first_bbox = None
                    st.streak_last_bbox = None
                    if not spatial_hits:
                        st.clear_streak += 1
                        if st.present and st.clear_streak >= cfg.clear_frames:
                            st.present = False
                    else:
                        st.clear_streak = 0
                    # No active novel arrival: keep reference frame fresh
                    self._update_reference_gray(st, cur_gray)
            else:
                # Legacy mode for minimal detection dicts without full_bbox
                trigger_hits = [
                    d for d in present_hits if float(d.get("confidence", 0.0)) >= cfg.confidence
                ]
                if present_hits:
                    st.clear_streak = 0
                    if not st.present and trigger_hits:
                        st.hit_streak += 1
                        if st.hit_streak >= cfg.confirm_frames:
                            st.present = True
                            muted = ts < self._mute_until
                            cooled = (ts - st.last_alert_ts) >= cfg.cooldown_seconds
                            if not muted and cooled:
                                top = trigger_hits[0]
                                pending_alert_info = (
                                    str(top["label"]),
                                    top["confidence"],
                                    None,
                                )
                    elif not st.present:
                        st.hit_streak = 0
                else:
                    st.hit_streak = 0
                    st.clear_streak += 1
                    if st.present and st.clear_streak >= cfg.clear_frames:
                        st.present = False
                self._update_reference_gray(st, cur_gray)

        if pending_alert_info is not None:
            label, confidence, _ = pending_alert_info
            if cfg.vision_verify and jpeg_bytes is not None:
                if not _verify_arrival_with_vision(jpeg_bytes, cfg.name):
                    return None
            with self._lock:
                st = self._state
                st.last_alert_ts = ts
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
                    "confidence": confidence,
                    "message": msg,
                    "has_snapshot": jpeg_bytes is not None,
                }
                st.next_alert_id = alert_id + 1
                st.alerts.append(alert)
                if jpeg_bytes is not None:
                    self._latest_jpeg = jpeg_bytes

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
                hits = self.detect_frame(frame, session=sess)
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
