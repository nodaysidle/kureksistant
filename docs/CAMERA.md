# Local RTSP camera watcher

The camera watcher is opt-in. It reads one RTSP stream, runs a small ONNX detector on the CPU, and alerts when a vehicle enters a region you set. No camera cloud is involved.

## Configuration

Create `~/.config/kurek/camera.json` and restrict it to your user (`chmod 600`). Replace the placeholder URL with your camera's address and credentials.

```json
{
  "enabled": true,
  "name": "Entrance",
  "rtsp_url": "rtsp://user:password@camera-address:554/stream",
  "roi": [0.14, 0.15, 0.72, 0.88],
  "poll_seconds": 2.0,
  "confidence": 0.45,
  "confirm_frames": 2,
  "clear_frames": 3,
  "cooldown_seconds": 60,
  "speak_on_pc": true,
  "model_path": "~/.local/share/kurek/models/yolov8n.onnx"
}
```

| Key | Default in code | Meaning |
|---|---|---|
| `roi` | `[0.14, 0.15, 0.72, 0.88]` | Region of interest as `[x1, y1, x2, y2]` in fractions of the frame. |
| `poll_seconds` | `2.0` | Seconds between frames. The minimum is 0.5. |
| `confidence` | `0.45` | Detection threshold. |
| `confirm_frames` | `2` | Consecutive detections needed before an arrival is reported. |
| `clear_frames` | `3` | Consecutive frames with no vehicle needed before the next arrival can be reported. |
| `cooldown_seconds` | `60` | Minimum gap between alerts. |
| `speak_on_pc` | `true` | Speak the alert on the PC as well as showing it. |
| `model_path` | `~/.local/share/kurek/models/yolov8n.onnx` | The YOLOv8n ONNX model. |

## Behaviour

- **Vehicles only.** The detector keeps COCO classes car, motorcycle, bus and truck inside `roi`. Animals and vehicles outside the region are ignored.
- **One alert per arrival.** A vehicle that stays parked does not trigger repeated alerts.
- **PC alert.** Shows a desktop notification with the snapshot through `notify-send`, and speaks the alert if `speak_on_pc` is set.
- **Phone alert.** Pushes a photo notification to Kurek Remote over Tailscale through `/remote/alerts`. The snapshot comes from `/remote/camera/snapshot`.
- **Archive.** Timestamped snapshots go to `~/Downloads/KUREK/doorcam/` on the PC and to the phone's `Pictures/doorcam/`.

## Commands

Ask Kurek, or use the Kurek Remote app:

- "Check the entrance camera" for a live check.
- "Mute entrance alerts for an hour", and "unmute entrance alerts".
- "Entrance camera status".

The camera name in these phrases is the `name` in your `camera.json`.
