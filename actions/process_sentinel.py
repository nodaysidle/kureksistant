"""
actions/process_sentinel.py — Process exit & long-build sentinel for Kurek.
Auto-discovered by core/action_loader.py.

Capabilities:
  • 'watch': Monitors a process (by PID, name, or auto-detected high-CPU job).
             When it terminates, notifies via notify-send and speaks an alert via Grok Sol TTS.
  • 'list': Lists currently watched jobs.
  • 'cancel': Stops monitoring a specific PID or all jobs.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import psutil

WATCHED_JOBS: dict[int, dict] = {}
_LOCK = threading.Lock()

ICON_PATH = "/home/arch/.local/share/icons/kurek.png"


def _format_elapsed(seconds: float) -> str:
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}h {mins}m {secs}s"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def _notify_job_complete(label: str, elapsed_str: str, pid: int):
    # 1. Desktop Notification
    try:
        title = "Job Completed"
        msg = f"{label} (PID {pid}) finished in {elapsed_str}."
        subprocess.run(
            ["notify-send", "-u", "normal", "-i", ICON_PATH, title, msg],
            timeout=3,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[Sentinel Notification Error] {e}")

    # 2. Append to Muse Daily Memory
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        daily_file = Path.home() / "memory" / f"{today}.md"
        daily_file.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"\n- [{ts}] **[JOB_COMPLETED]** {label} (PID {pid}) finished in {elapsed_str}.\n"
        with open(daily_file, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"[Sentinel Memory Log Error] {e}")

    # 3. Verbal Spoken Alert via xAI Grok TTS / System Audio
    spoken_text = f"Alan, your {label} job just finished in {elapsed_str}."
    try:
        from memory.config_manager import get_xai_key
        from core.tts import create_tts_player
        xai_key = get_xai_key()
        if xai_key:
            tts = create_tts_player({"tts_engine": "xai", "tts_voice": "sal", "xai_api_key": xai_key})
            tts.speak(spoken_text)
        else:
            tts = create_tts_player({"tts_engine": "mac_say"})
            tts.speak(spoken_text)
    except Exception as e:
        print(f"[Sentinel Speech Error] {e}")


def _watcher_worker(pid: int, label: str, start_time: float):
    print(f"[Sentinel] ⏳ Started monitoring '{label}' (PID {pid})", flush=True)
    while True:
        time.sleep(1.5)
        with _LOCK:
            if pid not in WATCHED_JOBS:
                print(f"[Sentinel] Monitoring cancelled for PID {pid}", flush=True)
                break

        # Check if process is still running
        if not psutil.pid_exists(pid):
            elapsed = time.time() - start_time
            elapsed_str = _format_elapsed(elapsed)
            print(f"[Sentinel] 🏁 Process {pid} ({label}) terminated after {elapsed_str}!", flush=True)
            with _LOCK:
                WATCHED_JOBS.pop(pid, None)
            _notify_job_complete(label, elapsed_str, pid)
            break


def _find_target_process(target: str | None = None) -> tuple[int | None, str]:
    """Finds a target PID by process name or high-CPU user job."""
    current_pid = os.getpid()

    # 1. Direct PID lookup
    if target and target.isdigit():
        pid = int(target)
        if psutil.pid_exists(pid):
            try:
                proc = psutil.Process(pid)
                return pid, proc.name()
            except Exception:
                return pid, f"PID {pid}"

    # 2. Process name match (e.g. 'cargo', 'npm', 'rustc', 'python', 'make')
    if target and target.lower() not in ("auto", "active", "build"):
        t_lower = target.lower()
        candidates = []
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                if p.pid == current_pid:
                    continue
                name = p.info.get("name", "").lower()
                cmd = " ".join(p.info.get("cmdline") or []).lower()
                if t_lower in name or t_lower in cmd:
                    candidates.append((p.info["create_time"], p.pid, p.info.get("name", target)))
            except Exception:
                pass
        if candidates:
            # Pick most recently launched
            candidates.sort(reverse=True)
            return candidates[0][1], candidates[0][2]

    # 3. Auto-detect highest CPU consumer or active build tool
    build_tools = ("cargo", "rustc", "npm", "node", "python", "make", "clang", "gcc", "docker", "webpack", "tsc")
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            if p.pid == current_pid:
                continue
            name = (p.info.get("name") or "").lower()
            if any(bt in name for bt in build_tools):
                return p.pid, p.info.get("name", "build job")
        except Exception:
            pass

    return None, ""


def process_sentinel(parameters: dict, **kwargs) -> str:
    params = parameters or {}
    action = params.get("action", "watch").lower()

    if action == "list":
        with _LOCK:
            if not WATCHED_JOBS:
                return "No processes currently being watched."
            lines = [f"• PID {pid}: {info['label']} (running for {_format_elapsed(time.time() - info['start_time'])})" for pid, info in WATCHED_JOBS.items()]
            return "Active watched jobs:\n" + "\n".join(lines)

    if action == "cancel":
        target = str(params.get("target", "")).strip()
        with _LOCK:
            if target.isdigit() and int(target) in WATCHED_JOBS:
                WATCHED_JOBS.pop(int(target), None)
                return f"Cancelled monitoring for PID {target}."
            if target == "all":
                WATCHED_JOBS.clear()
                return "Cancelled all watched jobs."
            return f"No watched job matching target '{target}'."

    # Default: watch
    target = params.get("target")
    label_override = params.get("label", "").strip()

    pid, proc_name = _find_target_process(str(target) if target else None)
    if not pid:
        return "Could not find a matching active process to watch. Provide a process name (e.g. 'cargo', 'npm') or PID."

    label = label_override or proc_name or f"Process {pid}"

    with _LOCK:
        if pid in WATCHED_JOBS:
            return f"Already watching {label} (PID {pid})."
        start_time = time.time()
        WATCHED_JOBS[pid] = {
            "label": label,
            "start_time": start_time,
        }

    t = threading.Thread(target=_watcher_worker, args=(pid, label, start_time), daemon=True)
    t.start()

    return f"Watching {label} (PID {pid}). I will alert you verbally and via notification the moment it finishes."


TOOL = {
    "name": "process_sentinel",
    "description": "Watch a long-running compile, build, test, or compute process (like cargo build, npm, python training, or make). When the process terminates, Kurek sends an alert and verbally speaks the completion time. Use action='watch' with a process name, command, or PID, or action='list'.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'watch' to start monitoring, 'list' to see active watchers, or 'cancel' to stop",
                "enum": ["watch", "list", "cancel"]
            },
            "target": {
                "type": "STRING",
                "description": "Process name (e.g. 'cargo', 'npm', 'python', 'rustc') or numeric PID. Can be omitted for auto-detection."
            },
            "label": {
                "type": "STRING",
                "description": "Optional human-friendly description (e.g. 'Sonora Release Build', 'Model Training')"
            }
        },
        "required": ["action"]
    },
    "handler": process_sentinel,
}
