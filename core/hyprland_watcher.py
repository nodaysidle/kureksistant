"""
core/hyprland_watcher.py — Event-driven Hyprland state watcher via socket2.sock.

Listens to Hyprland's broadcast event stream on .socket2.sock and keeps an
in-memory cache of focused window class, title, address, and workspace.

hyprctl usage:
  • one seed call at startup (activewindow -j)
  • one lazy geometry fetch per vision/screen-capture request when geometry
    is stale (focus/move events mark geometry dirty; they do NOT spawn hyprctl)
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path


def _resolve_hyprland_socket2() -> tuple[str | None, str | None]:
    """
    Finds the active Hyprland instance signature and its .socket2.sock.
    Handles stale environment variables and multiple sessions gracefully.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid() if hasattr(os, 'getuid') else 1000}"
    preferred_his = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")

    # Try preferred first
    if preferred_his:
        candidate = Path(xdg) / "hypr" / preferred_his / ".socket2.sock"
        if candidate.exists():
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(0.1)
                s.connect(str(candidate))
                s.close()
                return str(candidate), preferred_his
            except Exception:
                pass

    # Search all instance sockets under xdg/hypr/
    for sock in glob.glob(f"{xdg}/hypr/*/.socket2.sock"):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.1)
            s.connect(sock)
            s.close()
            inst_sig = Path(sock).parent.name
            return sock, inst_sig
        except Exception:
            continue

    return None, None


class HyprlandEventWatcher:
    """Persistent background listener for Hyprland's .socket2.sock."""

    def __init__(self):
        self._lock = threading.Lock()
        self._active_window: dict = {
            "class": "",
            "title": "",
            "address": "",
            "at": [0, 0],
            "size": [0, 0],
            "workspace": {"id": 1, "name": "1"},
        }
        self._geometry_dirty = True
        self._sock_path: str | None = None
        self._instance_sig: str | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        # Test seam: count lazy geometry fetches.
        self.geometry_fetch_count = 0

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True

        self._seed_initial_state()
        self._thread = threading.Thread(target=self._listen_loop, daemon=True, name="HyprlandSocket2")
        self._thread.start()

    def _seed_initial_state(self):
        """Seed initial active window (incl. geometry) from hyprctl once on startup."""
        sock_path, inst_sig = _resolve_hyprland_socket2()
        self._sock_path = sock_path
        self._instance_sig = inst_sig

        if not inst_sig or not shutil.which("hyprctl"):
            return

        try:
            data = self._hyprctl_activewindow()
            if data:
                with self._lock:
                    self._active_window.update(data)
                    self._geometry_dirty = False
        except Exception as e:
            print(f"[HyprWatcher] Seed notice: {e}")

    def _hyprctl_activewindow(self) -> dict | None:
        if not self._instance_sig or not shutil.which("hyprctl"):
            return None
        cmd = ["hyprctl", "--instance", self._instance_sig, "activewindow", "-j"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1.0)
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout)
        return None

    def _fetch_geometry_once(self) -> None:
        """Synchronous one-shot geometry refresh for vision/capture callers."""
        try:
            data = self._hyprctl_activewindow()
        except Exception:
            data = None
        with self._lock:
            self.geometry_fetch_count += 1
            if data:
                self._active_window.update(data)
                self._geometry_dirty = False

    def apply_event_line(self, line: str) -> None:
        """Parse a single socket2 line into the cache. Never spawns hyprctl."""
        line = line.strip()
        if not line or ">>" not in line:
            return
        event_type, event_data = line.split(">>", 1)
        self._handle_event(event_type, event_data)

    def _handle_event(self, event_type: str, event_data: str) -> None:
        if event_type == "activewindow":
            # class,title
            parts = event_data.split(",", 1)
            win_cls = parts[0]
            win_title = parts[1] if len(parts) > 1 else ""
            with self._lock:
                self._active_window["class"] = win_cls
                self._active_window["title"] = win_title
                self._geometry_dirty = True

        elif event_type == "activewindowv2":
            # address (e.g. 0x5b4992764370)
            with self._lock:
                self._active_window["address"] = event_data.strip()
                self._geometry_dirty = True

        elif event_type == "openwindow":
            # address,workspace,class,title
            parts = event_data.split(",", 3)
            if len(parts) < 4:
                return
            addr, workspace, win_cls, win_title = parts
            with self._lock:
                # Update cache when this is (or becomes) the focused window.
                current_addr = (self._active_window.get("address") or "").lower()
                if not current_addr or current_addr == addr.lower():
                    self._active_window["address"] = addr
                    self._active_window["class"] = win_cls
                    self._active_window["title"] = win_title
                    self._active_window.setdefault("workspace", {})["name"] = workspace
                    self._geometry_dirty = True

        elif event_type in ("movewindow", "movewindowv2"):
            # address,workspace  (movewindowv2 may include more fields)
            parts = event_data.split(",", 1)
            if not parts:
                return
            addr = parts[0].strip()
            workspace = parts[1].strip() if len(parts) > 1 else ""
            with self._lock:
                current_addr = (self._active_window.get("address") or "").lower()
                if current_addr and current_addr == addr.lower():
                    if workspace:
                        # workspace may be "name" or "id,name"
                        ws_name = workspace.split(",")[-1]
                        self._active_window.setdefault("workspace", {})["name"] = ws_name
                    self._geometry_dirty = True

        elif event_type == "workspace":
            with self._lock:
                self._active_window.setdefault("workspace", {})["name"] = event_data

        elif event_type == "workspacev2":
            # id,name
            parts = event_data.split(",", 1)
            with self._lock:
                ws = self._active_window.setdefault("workspace", {})
                if parts[0].strip().lstrip("-").isdigit():
                    ws["id"] = int(parts[0].strip())
                if len(parts) > 1:
                    ws["name"] = parts[1]

    def _listen_loop(self):
        while self._running:
            if not self._sock_path or not os.path.exists(self._sock_path):
                self._sock_path, self._instance_sig = _resolve_hyprland_socket2()
                if not self._sock_path:
                    time.sleep(2.0)
                    continue

            sock = None
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(self._sock_path)
                sock_file = sock.makefile("r", encoding="utf-8", errors="replace")

                print(f"[HyprWatcher] 🚀 Connected to Hyprland event socket: {self._sock_path}", flush=True)

                for line in sock_file:
                    if not self._running:
                        break
                    self.apply_event_line(line)

            except Exception:
                # Socket disconnected or hyprland reloaded
                time.sleep(1.0)
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

    def get_active_window_context(self) -> dict:
        """O(1) in-memory lookup (class/title/workspace/address). No hyprctl."""
        with self._lock:
            return dict(self._active_window)

    def get_active_window_geometry(self) -> tuple[int, int, int, int] | None:
        """
        Returns (x, y, width, height) if valid.

        Fetches geometry via hyprctl at most once while dirty (typically once
        per vision/screen-capture request after a focus change).
        """
        with self._lock:
            dirty = self._geometry_dirty
            at = list(self._active_window.get("at", [0, 0]) or [0, 0])
            size = list(self._active_window.get("size", [0, 0]) or [0, 0])
            have_geom = (
                len(at) == 2
                and len(size) == 2
                and size[0] > 50
                and size[1] > 50
            )

        if dirty or not have_geom:
            self._fetch_geometry_once()
            with self._lock:
                at = list(self._active_window.get("at", [0, 0]) or [0, 0])
                size = list(self._active_window.get("size", [0, 0]) or [0, 0])
                have_geom = (
                    len(at) == 2
                    and len(size) == 2
                    and size[0] > 50
                    and size[1] > 50
                )

        if have_geom:
            return int(at[0]), int(at[1]), int(size[0]), int(size[1])
        return None

    def stop(self):
        with self._lock:
            self._running = False


_GLOBAL_WATCHER: HyprlandEventWatcher | None = None


def get_hyprland_watcher() -> HyprlandEventWatcher:
    global _GLOBAL_WATCHER
    if _GLOBAL_WATCHER is None:
        _GLOBAL_WATCHER = HyprlandEventWatcher()
        _GLOBAL_WATCHER.start()
    return _GLOBAL_WATCHER
