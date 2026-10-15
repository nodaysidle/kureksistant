"""Regression tests for the mpv process leak in core.mpv_sink.

MpvPipeWireSink used to spawn mpv with ``start_new_session=True``, which put it
in its own session and process group. ``scripts/verify.sh`` reaps the daemon
under test with a *process-group* kill, so mpv was never in the group that got
signalled; combined with ``--idle`` (no timeout) each verify run orphaned one
mpv process forever. Observed in the wild: 20 leaked mpv processes (~250 MB)
accumulated over 21 h.

These tests are hermetic: no real mpv binary, no real Unix sockets.
"""
from __future__ import annotations

from typing import Any

from core import mpv_sink


class _FakeProc:
    """Minimal stand-in for ``subprocess.Popen``."""

    def __init__(self, cmd: list[str], **kwargs: Any) -> None:
        self.cmd = cmd
        self.kwargs = kwargs
        self.terminated = False
        self.killed = False
        self.waited = False
        self._returncode: int | None = None

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = 0

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    def wait(self, timeout: float | None = None) -> int | None:
        self.waited = True
        if self._returncode is None:
            self._returncode = 0
        return self._returncode


def _patch_mpv(monkeypatch: Any) -> list[_FakeProc]:
    """Fake mpv availability, Popen, and the liveness probe.

    The faked probe reports "not alive" until a process has been spawned, so
    ``ensure_running()`` starts exactly one mpv and then considers it healthy.
    """
    spawned: list[_FakeProc] = []

    def fake_popen(cmd: list[str], **kwargs: Any) -> _FakeProc:
        proc = _FakeProc(cmd, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(mpv_sink.shutil, "which", lambda _name: "/usr/bin/mpv")
    monkeypatch.setattr(mpv_sink.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        mpv_sink.MpvPipeWireSink,
        "_is_socket_alive",
        lambda self: bool(spawned),
    )
    return spawned


def test_mpv_is_not_detached_into_its_own_session(monkeypatch: Any, tmp_path: Any) -> None:
    """The regression itself: a detached mpv survives a process-group kill."""
    spawned = _patch_mpv(monkeypatch)
    sink = mpv_sink.MpvPipeWireSink(sock_path=tmp_path / "kurek_mpv.sock")

    assert sink.ensure_running() is True
    assert len(spawned) == 1

    proc = spawned[0]
    assert proc.kwargs.get("start_new_session") is not True, (
        "mpv must stay in the daemon's process group, otherwise verify.sh's "
        "group kill cannot reap it and it leaks"
    )
    assert proc.cmd[0] == "mpv"
    assert f"--input-ipc-server={sink.sock_path}" in proc.cmd

    sink.shutdown()


def test_shutdown_terminates_mpv_and_removes_its_socket(monkeypatch: Any, tmp_path: Any) -> None:
    spawned = _patch_mpv(monkeypatch)
    sock = tmp_path / "kurek_mpv.sock"
    sink = mpv_sink.MpvPipeWireSink(sock_path=sock)
    assert sink.ensure_running() is True

    sock.write_text("")  # pretend the socket file is present on disk
    sink.shutdown()

    proc = spawned[0]
    assert proc.terminated is True
    assert proc.waited is True
    assert proc.killed is False, "a clean terminate() should not need kill()"
    assert sink._proc is None
    assert not sock.exists(), "shutdown() should remove the socket it owns"


def test_shutdown_never_respawns_mpv(monkeypatch: Any, tmp_path: Any) -> None:
    """Guard the ensure=False path: teardown must not start a replacement mpv."""
    spawned = _patch_mpv(monkeypatch)
    sink = mpv_sink.MpvPipeWireSink(sock_path=tmp_path / "kurek_mpv.sock")
    sink._proc = _FakeProc(["mpv"])

    sink.shutdown()

    assert spawned == [], "shutdown() must not call ensure_running()"
    assert sink._proc is None


def test_shutdown_is_idempotent_without_a_process(monkeypatch: Any, tmp_path: Any) -> None:
    """Hermetic: the old shutdown() called ensure_running() and started a real mpv."""
    spawned = _patch_mpv(monkeypatch)
    sink = mpv_sink.MpvPipeWireSink(sock_path=tmp_path / "absent.sock")
    sink.shutdown()
    sink.shutdown()  # must not raise
    assert sink._proc is None
    assert spawned == [], "shutdown() with no running mpv must not start one"


def test_ensure_running_reuses_an_alive_mpv(monkeypatch: Any, tmp_path: Any) -> None:
    spawned = _patch_mpv(monkeypatch)
    sink = mpv_sink.MpvPipeWireSink(sock_path=tmp_path / "kurek_mpv.sock")

    assert sink.ensure_running() is True
    assert sink.ensure_running() is True
    assert len(spawned) == 1, "a healthy mpv must not be replaced"

    sink.shutdown()


def test_already_exited_process_is_reaped_before_respawn(monkeypatch: Any, tmp_path: Any) -> None:
    spawned = _patch_mpv(monkeypatch)
    sink = mpv_sink.MpvPipeWireSink(sock_path=tmp_path / "kurek_mpv.sock")
    corpse = _FakeProc(["mpv"])
    corpse._returncode = 1  # exited
    sink._proc = corpse

    assert sink.ensure_running() is True
    assert sink._proc is not corpse
    assert len(spawned) == 1

    sink.shutdown()
