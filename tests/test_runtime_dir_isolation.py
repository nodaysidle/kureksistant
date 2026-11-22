"""Every test runs with a private XDG_RUNTIME_DIR (conftest), so the real TTSPlayer.stop() and the
daemon socket never reach the live Kurek's mpv or kurek.sock."""
from __future__ import annotations

import os
import stat
from pathlib import Path


def test_mpv_socket_resolves_inside_the_test_runtime_dir(tmp_path_factory) -> None:
    from core import mpv_sink

    run_dir = Path(os.environ["XDG_RUNTIME_DIR"])
    assert run_dir.is_relative_to(tmp_path_factory.getbasetemp())
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert mpv_sink.get_mpv_sink().sock_path == run_dir / "kurek_mpv.sock"
