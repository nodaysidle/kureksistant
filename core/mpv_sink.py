"""
core/mpv_sink.py — Persistent PipeWire mpv audio sink with IPC socket control.

Avoids per-utterance process startup latency and provides instant barge-in /
stop functionality via $XDG_RUNTIME_DIR/kurek_mpv.sock.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from core.timing import mark as timing_mark


def _get_runtime_dir() -> Path:
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg and Path(xdg).exists():
        return Path(xdg)
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    p = Path(f"/run/user/{uid}")
    if p.exists():
        return p
    return Path("/tmp")


class MpvPipeWireSink:
    """Manages a background mpv process communicating via Unix Domain Socket IPC."""

    def __init__(self, sock_path: str | Path | None = None):
        self.sock_path = Path(sock_path) if sock_path else _get_runtime_dir() / "kurek_mpv.sock"
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._chunk_counter = 0

    def _is_socket_alive(self) -> bool:
        if not self.sock_path.exists():
            return False
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.15)
            s.connect(str(self.sock_path))
            cmd = json.dumps({"command": ["get_property", "idle-active"]}).encode() + b"\n"
            s.sendall(cmd)
            resp = s.recv(1024)
            s.close()
            return b"idle-active" in resp or b"success" in resp
        except Exception:
            return False

    def ensure_running(self) -> bool:
        with self._lock:
            if self._is_socket_alive():
                return True

            # Reap a previous mpv that has already exited so dead Popen objects
            # (and their zombies) don't accumulate across restarts.
            if self._proc is not None and self._proc.poll() is not None:
                self._proc = None

            if not shutil.which("mpv"):
                return False

            # Clean stale socket
            if self.sock_path.exists():
                try:
                    self.sock_path.unlink()
                except Exception:
                    pass

            cmd = [
                "mpv",
                "--idle",
                "--no-terminal",
                "--really-quiet",
                "--ao=pipewire",
                f"--input-ipc-server={self.sock_path}",
            ]
            try:
                # Deliberately NO start_new_session=True. That detaches mpv into
                # its own session/process group, so any group-kill of the daemon
                # (scripts/verify.sh cleanup, Ctrl-C in a foreground run) missed
                # it; combined with --idle it then lived forever. Observed: 20
                # orphaned mpv processes (~250 MB) accumulated over 21 h, one per
                # verify.sh run. Staying in the daemon's group means the group
                # kill reaps it, and systemd's cgroup kill already covered the
                # service case.
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
                # Wait briefly for socket to become ready
                for _ in range(25):
                    time.sleep(0.02)
                    if self._is_socket_alive():
                        return True
            except Exception as e:
                print(f"[MpvSink] Failed to start mpv: {e}")
                return False

            return self._is_socket_alive()

    def _send_command(self, cmd: list, ensure: bool = True) -> bool:
        """Send an IPC command to the mpv process.

        ensure=True (the default) starts mpv on demand. Pass ensure=False for
        teardown, where respawning mpv would defeat the purpose.
        """
        if ensure:
            if not self.ensure_running():
                return False
        elif not self.sock_path.exists():
            return False
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.3)
            s.connect(str(self.sock_path))
            payload = json.dumps({"command": cmd}).encode() + b"\n"
            s.sendall(payload)
            s.close()
            return True
        except Exception as e:
            print(f"[MpvSink IPC Error] {e}")
            return False

    def play_bytes(self, audio_bytes: bytes, append: bool = False) -> bool:
        """
        Plays audio bytes via mpv IPC.
        Uses in-memory tmpfs (/dev/shm) to eliminate NVMe/disk writes completely.
        """
        if not audio_bytes:
            return False

        if not self.ensure_running():
            return False

        # Use /dev/shm (RAM tmpfs) or fallback to runtime dir
        shm_dir = Path("/dev/shm") if Path("/dev/shm").exists() else _get_runtime_dir()
        with self._lock:
            self._chunk_counter = (self._chunk_counter + 1) % 1000
            chunk_file = shm_dir / f"kurek_tts_{self._chunk_counter}.mp3"

        try:
            chunk_file.write_bytes(audio_bytes)
        except Exception as e:
            print(f"[MpvSink] Failed to write to {chunk_file}: {e}")
            return False

        mode = "append-play" if append else "replace"
        timing_mark("mpv_loadfile", once=True)
        success = self._send_command(["loadfile", str(chunk_file), mode])

        # Clean old chunks from /dev/shm in background. A "replace" starts a new
        # utterance and flushes mpv's playlist, so every earlier chunk is done
        # with. While appending, earlier chunks of this reply may still be
        # queued in mpv, so only reap clearly abandoned ones.
        max_age = 0 if mode == "replace" else 600

        def _cleanup_old_chunks():
            try:
                now = time.time()
                for old in shm_dir.glob("kurek_tts_*.mp3"):
                    if old != chunk_file and (now - old.stat().st_mtime > max_age):
                        old.unlink(missing_ok=True)
            except Exception:
                pass

        threading.Thread(target=_cleanup_old_chunks, daemon=True).start()
        return success

    def stop(self) -> bool:
        """Instant barge-in / stop."""
        success = self._send_command(["stop"])
        # Clear playlist
        self._send_command(["playlist-clear"])
        return success

    def is_playing(self) -> bool:
        """Check if mpv is currently playing audio."""
        if not self.sock_path.exists():
            return False
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.1)
            s.connect(str(self.sock_path))
            cmd = json.dumps({"command": ["get_property", "idle-active"]}).encode() + b"\n"
            s.sendall(cmd)
            resp = json.loads(s.recv(1024).decode())
            s.close()
            # If idle-active is False, mpv is actively playing
            return resp.get("data") is False
        except Exception:
            return False

    def shutdown(self):
        """Shut down the background mpv process and remove its socket.

        Idempotent, and safe to call from an atexit hook. Uses ensure=False so
        teardown can never respawn mpv.
        """
        if self._proc is not None:
            self._send_command(["quit"], ensure=False)
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=2)
                except Exception:
                    pass
            self._proc = None

        # Remove the socket we own so the next start is clean.
        try:
            if self.sock_path.exists():
                self.sock_path.unlink()
        except Exception:
            pass


_GLOBAL_SINK: MpvPipeWireSink | None = None


def get_mpv_sink() -> MpvPipeWireSink:
    global _GLOBAL_SINK
    if _GLOBAL_SINK is None:
        _GLOBAL_SINK = MpvPipeWireSink()
        # Best-effort teardown on a clean interpreter exit (dev runs, tests).
        # atexit does not run on SIGTERM/SIGKILL, so this complements rather
        # than replaces the process-group / cgroup kill that reaps mpv.
        atexit.register(_GLOBAL_SINK.shutdown)
    return _GLOBAL_SINK
