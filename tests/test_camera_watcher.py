"""Isolated unit tests for core/camera_watcher.py and actions/camera_watch.py."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from actions.camera_watch import TOOL, camera_watch
from core.camera_watcher import (
    CameraConfig,
    CameraWatcher,
    detect_vehicles,
    load_camera_config,
)


def _make_jpeg(
    size: tuple[int, int] = (320, 180),
    color: tuple[int, int, int] = (40, 50, 60),
    rect: tuple[float, float, float, float] | None = None,
    rect_color: tuple[int, int, int] = (210, 210, 210),
) -> bytes:
    img = Image.new("RGB", size, color)
    if rect is not None:
        draw = ImageDraw.Draw(img)
        w, h = size
        draw.rectangle(
            [rect[0] * w, rect[1] * h, rect[2] * w, rect[3] * h],
            fill=rect_color,
        )
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


class _FakeInput:
    name = "images"


class _FakeSession:
    """Returns synthetic YOLOv8 (1, 84, 8400) predictions with configurable boxes."""

    def __init__(
        self,
        detections: dict[int, float] | list[tuple[int, float, tuple[float, float, float, float]]],
    ) -> None:
        if isinstance(detections, dict):
            # Default valid centered courtyard box in 640x640 letterboxed space
            self._dets = [
                (cid, score, (320.0, 340.0, 260.0, 180.0))
                for cid, score in detections.items()
            ]
        else:
            self._dets = list(detections)

    def get_inputs(self):
        return [_FakeInput()]

    def run(self, _outputs, _feeds):
        preds = np.zeros((1, 84, 8400), dtype=np.float32)
        for idx, (cid, score, (cx, cy, bw, bh)) in enumerate(self._dets):
            preds[0, 0, idx] = cx
            preds[0, 1, idx] = cy
            preds[0, 2, idx] = bw
            preds[0, 3, idx] = bh
            preds[0, 4 + cid, idx] = score
        return [preds]


def test_detect_vehicles_ignores_cats_and_keeps_cars():
    jpeg = _make_jpeg()
    # Non-overlapping cat (0.95), car (0.72), person (0.88)
    sess = _FakeSession(
        [
            (15, 0.95, (100.0, 400.0, 120.0, 100.0)),
            (2, 0.72, (360.0, 340.0, 220.0, 150.0)),
            (0, 0.88, (540.0, 340.0, 80.0, 160.0)),
        ]
    )
    hits = detect_vehicles(jpeg, sess, roi=(0.1, 0.1, 0.9, 0.9), conf_thresh=0.45)
    assert len(hits) == 1
    assert hits[0]["label"] == "car"
    assert hits[0]["confidence"] == 0.72
    assert "bbox" in hits[0] and "full_bbox" in hits[0]
    assert hits[0]["area"] >= 0.04


def test_detect_vehicles_cat_only_returns_empty():
    jpeg = _make_jpeg()
    sess = _FakeSession({15: 0.99})
    hits = detect_vehicles(jpeg, sess, conf_thresh=0.45)
    assert hits == []


def test_detect_vehicles_nms_and_non_vehicle_conflict():
    jpeg = _make_jpeg()
    # Two overlapping car/truck boxes for the same vehicle -> NMS keeps highest conf only
    # Plus a second "car" heavily overlapped by a higher-confidence cat -> suppressed
    sess = _FakeSession(
        [
            (2, 0.78, (320.0, 340.0, 220.0, 150.0)),  # car
            (7, 0.69, (325.0, 342.0, 215.0, 148.0)),  # overlapping truck
            (2, 0.60, (140.0, 360.0, 140.0, 120.0)),  # false car on cat
            (15, 0.85, (142.0, 360.0, 138.0, 120.0)),  # real cat
        ]
    )
    hits = detect_vehicles(jpeg, sess, roi=(0.1, 0.1, 0.9, 0.9), conf_thresh=0.45)
    assert len(hits) == 1
    assert hits[0]["label"] == "car"
    assert hits[0]["confidence"] == 0.78


def test_detect_vehicles_geometry_filters_reject_neighbor_car_and_exclusions():
    jpeg = _make_jpeg(size=(640, 360))
    # Tiny box high in the frame (neighbor car behind stone wall: area ~ 0.01, y2 ~ 0.28)
    # Plus a genuine courtyard car (area ~ 0.25, y2 ~ 0.65)
    sess = _FakeSession(
        [
            (2, 0.66, (110.0, 195.0, 55.0, 35.0)),   # tiny neighbor car
            (2, 0.80, (340.0, 360.0, 280.0, 180.0)),  # courtyard car
        ]
    )
    hits = detect_vehicles(
        jpeg,
        sess,
        roi=(0.14, 0.18, 0.72, 0.88),
        conf_thresh=0.45,
        min_box_area=0.04,
        min_bottom_y=0.38,
    )
    assert len(hits) == 1
    assert hits[0]["confidence"] == 0.80
    assert hits[0]["full_bbox"][3] >= 0.38

    # Exclude zone covering the courtyard car suppresses it too
    hits_excl = detect_vehicles(
        jpeg,
        sess,
        roi=(0.14, 0.18, 0.72, 0.88),
        conf_thresh=0.45,
        min_box_area=0.04,
        min_bottom_y=0.38,
        exclude_zones=[(0.25, 0.30, 0.70, 0.85)],
    )
    assert hits_excl == []


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


def test_parked_car_hysteresis_cat_occlusion_and_startup_baseline(tmp_path: Path):
    cfg = CameraConfig(
        enabled=True,
        name="Okno Vhod",
        rtsp_url="rtsp://example/stream",
        confidence=0.65,
        clear_confidence=0.35,
        confirm_frames=2,
        clear_frames=3,
        cooldown_seconds=60.0,
        stationary_iou=0.40,
        stationary_clear_seconds=120.0,
        motion_threshold=8.0,
        startup_baseline_frames=2,
        speak_on_pc=False,
    )
    watcher = CameraWatcher(
        config=cfg,
        snapshot_path=tmp_path / "snap.jpg",
        doorcam_dir=tmp_path / "doorcam",
    )
    parked_box = [0.51, 0.23, 0.68, 0.56]
    jitter_box = [0.51, 0.22, 0.63, 0.48]  # partial pillar occlusion jitter
    parked_jpeg = _make_jpeg(rect=(0.51, 0.23, 0.68, 0.56))
    parked_hit = [{"label": "car", "confidence": 0.80, "full_bbox": parked_box}]
    flicker_hit = [{"label": "truck", "confidence": 0.42, "full_bbox": jitter_box}]

    with patch.object(watcher, "_emit_local_alert") as emit_mock:
        # Startup baseline frames seed the already-parked car without alerting
        assert watcher.process_detections(parked_hit, parked_jpeg, now=1000.0) is None
        assert watcher.process_detections(parked_hit, parked_jpeg, now=1002.0) is None
        assert watcher.status()["stationary_count"] == 1

        # IR flicker + bbox jitter + class flip stays suppressed
        assert watcher.process_detections(flicker_hit, parked_jpeg, now=1004.0) is None
        assert watcher.process_detections(parked_hit, parked_jpeg, now=1100.0) is None

        # Cat occludes parked car for 30s (< 120s stationary_clear_seconds)
        for step in range(15):
            assert watcher.process_detections([], parked_jpeg, now=1102.0 + step * 2.0) is None

        # Cat leaves and parked car reappears -> still suppressed!
        assert watcher.process_detections(parked_hit, parked_jpeg, now=1134.0) is None
        assert watcher.process_detections(parked_hit, parked_jpeg, now=1136.0) is None
        assert emit_mock.call_count == 0

        # Meanwhile, a NEW car drives into the middle of the courtyard -> DOES trigger!
        new_box = [0.20, 0.30, 0.50, 0.70]
        both_jpeg = _make_jpeg(rect=(0.20, 0.30, 0.50, 0.70), rect_color=(230, 230, 230))
        both_hits = [
            {"label": "car", "confidence": 0.85, "full_bbox": new_box},
            {"label": "car", "confidence": 0.80, "full_bbox": parked_box},
        ]
        assert watcher.process_detections(both_hits, both_jpeg, now=1200.0) is None
        alert = watcher.process_detections(both_hits, both_jpeg, now=1202.0)
        assert alert is not None
        assert alert["label"] == "car"
        assert emit_mock.call_count == 1


def test_true_arrival_then_departure_then_return_triggers_twice(tmp_path: Path):
    cfg = CameraConfig(
        enabled=True,
        name="Okno Vhod",
        rtsp_url="rtsp://example/stream",
        confidence=0.65,
        clear_confidence=0.35,
        confirm_frames=2,
        clear_frames=3,
        cooldown_seconds=60.0,
        stationary_iou=0.40,
        stationary_clear_seconds=120.0,
        motion_threshold=8.0,
        startup_baseline_frames=0,
        speak_on_pc=False,
    )
    watcher = CameraWatcher(
        config=cfg,
        snapshot_path=tmp_path / "snap.jpg",
        doorcam_dir=tmp_path / "doorcam",
    )
    empty_jpeg = _make_jpeg()
    car_box = [0.25, 0.28, 0.62, 0.66]
    car_jpeg = _make_jpeg(rect=(0.25, 0.28, 0.62, 0.66), rect_color=(220, 220, 220))
    car_hit = [{"label": "car", "confidence": 0.82, "full_bbox": car_box}]

    with patch.object(watcher, "_emit_local_alert") as emit_mock:
        # Establish empty courtyard reference frame
        assert watcher.process_detections([], empty_jpeg, now=1000.0) is None

        # Car arrives and stops within 2s (frame 1 & 2 both identical car_jpeg, diff vs frozen empty ref)
        assert watcher.process_detections(car_hit, car_jpeg, now=1002.0) is None
        alert1 = watcher.process_detections(car_hit, car_jpeg, now=1004.0)
        assert alert1 is not None
        assert emit_mock.call_count == 1

        # Car stays parked for 10 minutes -> no re-alert
        assert watcher.process_detections(car_hit, car_jpeg, now=1600.0) is None

        # Car leaves and stays gone for > 120s and >= 3 clear frames
        assert watcher.process_detections([], empty_jpeg, now=1602.0) is None
        assert watcher.process_detections([], empty_jpeg, now=1660.0) is None
        assert watcher.process_detections([], empty_jpeg, now=1730.0) is None
        assert watcher.status()["stationary_count"] == 0

        # Car returns to the exact same spot -> triggers alert #2!
        assert watcher.process_detections(car_hit, car_jpeg, now=1740.0) is None
        alert2 = watcher.process_detections(car_hit, car_jpeg, now=1742.0)
        assert alert2 is not None
        assert alert2["id"] > alert1["id"]
        assert emit_mock.call_count == 2


def test_motion_gate_suppresses_static_false_positive_without_change(tmp_path: Path):
    cfg = CameraConfig(
        enabled=True,
        name="Okno Vhod",
        rtsp_url="rtsp://example/stream",
        confidence=0.65,
        confirm_frames=2,
        motion_threshold=8.0,
        startup_baseline_frames=0,
        speak_on_pc=False,
    )
    watcher = CameraWatcher(
        config=cfg,
        snapshot_path=tmp_path / "snap.jpg",
        doorcam_dir=tmp_path / "doorcam",
    )
    static_jpeg = _make_jpeg()
    hit = [{"label": "car", "confidence": 0.75, "full_bbox": [0.25, 0.30, 0.55, 0.65]}]

    with patch.object(watcher, "_emit_local_alert") as emit_mock:
        # Seed reference frame with the exact same static image
        assert watcher.process_detections([], static_jpeg, now=1000.0) is None
        # Static hallucination with 0 pixel change inside bbox is suppressed
        assert watcher.process_detections(hit, static_jpeg, now=1002.0) is None
        assert watcher.process_detections(hit, static_jpeg, now=1004.0) is None
        assert emit_mock.call_count == 0


def test_detect_vehicles_min_full_area_and_foreground_vs_exclude_zone():
    jpeg = _make_jpeg(size=(640, 360))
    # ROI = (0.14, 0.18, 0.72, 0.88) -> crop is 371x252; letterbox scale=640/371=1.725, pad_y=102.6
    # Car 1: parked bay car around full=(0.39, 0.22, 0.70, 0.51), full_area ~ 0.09, y2=0.51
    # Car 2: big foreground car around full=(0.28, 0.21, 0.72, 0.70), full_area ~ 0.21, y2=0.70
    # In 640x640 letterbox coords:
    #   parked: cx~446, cy~222, bw~342, bh~180 -> full ~ (0.39, 0.22, 0.70, 0.51)
    #   big:    cx~395, cy~285, bw~480, bh~305 -> full ~ (0.28, 0.21, 0.72, 0.70)
    sess = _FakeSession(
        [
            (2, 0.82, (446.0, 222.0, 342.0, 180.0)),  # parked bay car
            (2, 0.79, (395.0, 285.0, 480.0, 305.0)),  # big foreground car
        ]
    )
    # Exclude zone covering parked gray car bay [0.37, 0.19, 0.73, 0.57] + min_full_area=0.14
    hits = detect_vehicles(
        jpeg,
        sess,
        roi=(0.14, 0.18, 0.72, 0.88),
        conf_thresh=0.55,
        min_full_area=0.14,
        exclude_zones=[(0.37, 0.19, 0.73, 0.57)],
    )
    assert len(hits) == 1
    assert hits[0]["full_area"] >= 0.14
    assert hits[0]["full_bbox"][3] > 0.57


def test_foreground_car_not_swallowed_by_parked_stationary_box(tmp_path: Path):
    cfg = CameraConfig(
        enabled=True,
        name="Okno Vhod",
        rtsp_url="rtsp://example/stream",
        confidence=0.55,
        confirm_frames=2,
        motion_threshold=8.0,
        stationary_iou=0.40,
        startup_baseline_frames=1,
        speak_on_pc=False,
    )
    watcher = CameraWatcher(
        config=cfg,
        snapshot_path=tmp_path / "snap.jpg",
        doorcam_dir=tmp_path / "doorcam",
    )
    parked_box = [0.39, 0.21, 0.70, 0.51]  # full_area=0.093, y2=0.51
    big_box = [0.28, 0.21, 0.72, 0.70]     # full_area=0.216, y2=0.70 (IoU=0.47, IoMin=0.94 with parked_box!)
    parked_jpeg = _make_jpeg(rect=(0.39, 0.21, 0.70, 0.51), rect_color=(120, 120, 120))
    big_jpeg = _make_jpeg(rect=(0.28, 0.21, 0.72, 0.70), rect_color=(240, 240, 240))

    with patch.object(watcher, "_emit_local_alert") as emit_mock:
        # Startup seeds the parked gray car
        assert watcher.process_detections(
            [{"label": "car", "confidence": 0.80, "full_bbox": parked_box}],
            parked_jpeg,
            now=1000.0,
        ) is None
        assert watcher.status()["stationary_count"] == 1

        # Big foreground car drives in front of parked car -> MUST trigger despite high IoU/IoMin
        big_hit = [{"label": "car", "confidence": 0.79, "full_bbox": big_box}]
        assert watcher.process_detections(big_hit, big_jpeg, now=1002.0) is None
        alert = watcher.process_detections(big_hit, big_jpeg, now=1004.0)
        assert alert is not None
        assert emit_mock.call_count == 1


def test_entry_direction_left_to_right_filters_backing_out(tmp_path: Path):
    cfg = CameraConfig(
        enabled=True,
        name="Okno Vhod",
        rtsp_url="rtsp://example/stream",
        confidence=0.55,
        confirm_frames=2,
        motion_threshold=8.0,
        entry_direction="left_to_right",
        min_move_x=0.015,
        entry_x_max=0.45,
        startup_baseline_frames=0,
        speak_on_pc=False,
    )
    watcher = CameraWatcher(config=cfg, snapshot_path=tmp_path / "snap.jpg")
    empty_jpeg = _make_jpeg()
    car_jpeg = _make_jpeg(rect=(0.15, 0.25, 0.70, 0.70), rect_color=(230, 230, 230))

    with patch.object(watcher, "_emit_local_alert") as emit_mock:
        assert watcher.process_detections([], empty_jpeg, now=1000.0) is None

        # Backing out right -> left (dx = -0.15) -> suppressed
        assert watcher.process_detections(
            [{"label": "car", "confidence": 0.8, "full_bbox": [0.35, 0.25, 0.65, 0.68]}],
            car_jpeg,
            now=1002.0,
        ) is None
        assert watcher.process_detections(
            [{"label": "car", "confidence": 0.8, "full_bbox": [0.20, 0.25, 0.50, 0.68]}],
            car_jpeg,
            now=1004.0,
        ) is None
        assert emit_mock.call_count == 0

        # Refresh empty courtyard
        assert watcher.process_detections([], empty_jpeg, now=1010.0) is None

        # Entering left -> right (dx = +0.12) -> triggers!
        assert watcher.process_detections(
            [{"label": "car", "confidence": 0.8, "full_bbox": [0.16, 0.22, 0.56, 0.70]}],
            car_jpeg,
            now=1012.0,
        ) is None
        alert = watcher.process_detections(
            [{"label": "car", "confidence": 0.8, "full_bbox": [0.28, 0.22, 0.68, 0.70]}],
            car_jpeg,
            now=1014.0,
        )
        assert alert is not None
        assert emit_mock.call_count == 1


def test_vision_verify_gate(tmp_path: Path):
    cfg = CameraConfig(
        enabled=True,
        name="Okno Vhod",
        rtsp_url="rtsp://example/stream",
        confidence=0.55,
        confirm_frames=1,
        motion_threshold=0.0,
        vision_verify=True,
        speak_on_pc=False,
    )
    watcher = CameraWatcher(config=cfg, snapshot_path=tmp_path / "snap.jpg")
    jpeg = _make_jpeg()
    hit = [{"label": "car", "confidence": 0.80, "full_bbox": [0.25, 0.22, 0.70, 0.70]}]

    with (
        patch.object(watcher, "_emit_local_alert") as emit_mock,
        patch("core.camera_watcher._verify_arrival_with_vision", return_value=False) as vmock,
    ):
        assert watcher.process_detections(hit, jpeg, now=1000.0) is None
        assert vmock.call_count == 1
        assert emit_mock.call_count == 0

    watcher2 = CameraWatcher(config=cfg, snapshot_path=tmp_path / "snap2.jpg")
    with (
        patch.object(watcher2, "_emit_local_alert") as emit_mock2,
        patch("core.camera_watcher._verify_arrival_with_vision", return_value=True) as vmock2,
    ):
        alert = watcher2.process_detections(hit, jpeg, now=1000.0)
        assert alert is not None
        assert vmock2.call_count == 1
        assert emit_mock2.call_count == 1


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
        json.dumps(
            {
                "enabled": True,
                "name": "Okno Vhod",
                "rtsp_url": "rtsp://x/1",
                "confidence": 0.65,
                "clear_confidence": 0.35,
                "min_box_area": 0.04,
                "min_full_area": 0.14,
                "min_bottom_y": 0.38,
                "exclude_zones": [[0.37, 0.19, 0.73, 0.57]],
                "startup_baseline_frames": 2,
                "entry_direction": "left_to_right",
                "min_move_x": 0.02,
                "entry_x_max": 0.42,
                "vision_verify": True,
            }
        ),
        encoding="utf-8",
    )
    cfg = load_camera_config(cfg_file)
    assert cfg is not None
    assert cfg.name == "Okno Vhod"
    assert cfg.clear_confidence == 0.35
    assert cfg.min_box_area == 0.04
    assert cfg.min_full_area == 0.14
    assert cfg.min_bottom_y == 0.38
    assert cfg.exclude_zones == [(0.37, 0.19, 0.73, 0.57)]
    assert cfg.startup_baseline_frames == 2
    assert cfg.entry_direction == "left_to_right"
    assert cfg.min_move_x == 0.02
    assert cfg.entry_x_max == 0.42
    assert cfg.vision_verify is True
    assert TOOL["name"] == "camera_watch"

    watcher = CameraWatcher(config=cfg, snapshot_path=tmp_path / "snap.jpg")
    with patch("actions.camera_watch.get_camera_watcher", return_value=watcher):
        out_mute = camera_watch({"action": "mute", "minutes": 45})
        assert "45 minutes" in out_mute
        out_status = camera_watch({"action": "status"})
        assert "Okno Vhod" in out_status
        out_unmute = camera_watch({"action": "unmute"})
        assert "Unmuted" in out_unmute
