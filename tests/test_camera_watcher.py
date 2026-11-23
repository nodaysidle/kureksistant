"""Isolated unit tests for core/camera_watcher.py and actions/camera_watch.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from actions.camera_watch import TOOL, camera_watch
from core.camera_watcher import (
    CameraConfig,
    CameraWatcher,
    detect_vehicles,
    load_camera_config,
)


def _make_jpeg(size: tuple[int, int] = (320, 180)) -> bytes:
    import io

    img = Image.new("RGB", size, (40, 50, 60))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class _FakeInput:
    name = "images"


class _FakeSession:
    """Returns synthetic YOLOv8 (1, 84, 8400) predictions for chosen COCO classes."""

    def __init__(self, class_scores: dict[int, float]) -> None:
        self._scores = class_scores

    def get_inputs(self):
        return [_FakeInput()]

    def run(self, _outputs, _feeds):
        preds = np.zeros((1, 84, 8400), dtype=np.float32)
        for idx, (cid, score) in enumerate(self._scores.items()):
            preds[0, 4 + cid, idx] = score
        return [preds]


def test_detect_vehicles_ignores_cats_and_keeps_cars():
    jpeg = _make_jpeg()
    # COCO 15 = cat (0.95), COCO 2 = car (0.72), COCO 0 = person (0.88)
    sess = _FakeSession({15: 0.95, 2: 0.72, 0: 0.88})
    hits = detect_vehicles(jpeg, sess, roi=(0.1, 0.1, 0.9, 0.9), conf_thresh=0.45)
    assert len(hits) == 1
    assert hits[0]["label"] == "car"
    assert hits[0]["confidence"] == 0.72


def test_detect_vehicles_cat_only_returns_empty():
    jpeg = _make_jpeg()
    sess = _FakeSession({15: 0.99})
    hits = detect_vehicles(jpeg, sess, conf_thresh=0.45)
    assert hits == []


def test_arrival_state_machine_requires_confirm_and_clear(tmp_path: Path):
    cfg = CameraConfig(
        enabled=True,
        name="Okno Vhod",
        rtsp_url="rtsp://example/stream",
        confirm_frames=2,
        clear_frames=2,
        cooldown_seconds=60.0,
        speak_on_pc=False,
    )
    watcher = CameraWatcher(
        config=cfg,
        snapshot_path=tmp_path / "snap.jpg",
        doorcam_dir=tmp_path / "doorcam",
    )
    car = [{"label": "car", "confidence": 0.8}]
    jpeg = _make_jpeg()

    with patch.object(watcher, "_emit_local_alert") as emit_mock:
        # 1st frame alone does not trigger
        assert watcher.process_detections(car, jpeg, now=1000.0) is None
        # 2nd consecutive frame triggers arrival alert
        alert = watcher.process_detections(car, jpeg, now=1002.0)
        assert alert is not None
        assert alert["id"] >= 1002
        assert alert["label"] == "car"
        assert emit_mock.call_count == 1

        # Parked car staying in frame does NOT re-trigger even past cooldown
        assert watcher.process_detections(car, jpeg, now=1100.0) is None
        assert watcher.process_detections(car, jpeg, now=1200.0) is None

        # Clear for 2 frames resets presence; next arrival triggers alert #2
        assert watcher.process_detections([], jpeg, now=1202.0) is None
        assert watcher.process_detections([], jpeg, now=1204.0) is None
        assert watcher.process_detections(car, jpeg, now=1206.0) is None
        alert2 = watcher.process_detections(car, jpeg, now=1208.0)
        assert alert2 is not None
        assert alert2["id"] > alert["id"]
        assert len(watcher.get_alerts_since(alert["id"])) == 1


def test_mute_suppresses_alerts(tmp_path: Path):
    cfg = CameraConfig(
        enabled=True,
        name="Okno Vhod",
        rtsp_url="rtsp://example/stream",
        confirm_frames=1,
        cooldown_seconds=5.0,
        speak_on_pc=False,
    )
    watcher = CameraWatcher(config=cfg, snapshot_path=tmp_path / "snap.jpg")
    watcher.mute(minutes=30)
    assert watcher.is_muted()
    with patch.object(watcher, "_emit_local_alert") as emit_mock:
        assert watcher.process_detections([{"label": "car", "confidence": 0.9}]) is None
        assert emit_mock.call_count == 0
    watcher.unmute()
    assert not watcher.is_muted()


def test_load_camera_config_and_tool_actions(tmp_path: Path):
    cfg_file = tmp_path / "camera.json"
    cfg_file.write_text(
        json.dumps({"enabled": True, "name": "Okno Vhod", "rtsp_url": "rtsp://x/1"}),
        encoding="utf-8",
    )
    cfg = load_camera_config(cfg_file)
    assert cfg is not None
    assert cfg.name == "Okno Vhod"
    assert TOOL["name"] == "camera_watch"

    watcher = CameraWatcher(config=cfg, snapshot_path=tmp_path / "snap.jpg")
    with patch("actions.camera_watch.get_camera_watcher", return_value=watcher):
        out_mute = camera_watch({"action": "mute", "minutes": 45})
        assert "45 minutes" in out_mute
        out_status = camera_watch({"action": "status"})
        assert "Okno Vhod" in out_status
        out_unmute = camera_watch({"action": "unmute"})
        assert "Unmuted" in out_unmute
