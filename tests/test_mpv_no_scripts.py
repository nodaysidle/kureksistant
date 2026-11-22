"""A7: Kurek's mpv loads no system-wide scripts, so mpv-mpris (media keys) cannot pause Kurek's speech.
Fake Popen only: no mpv is started."""
from __future__ import annotations

from core import mpv_sink
from tests.test_mpv_sink import _patch_mpv


def test_kureks_mpv_loads_no_scripts(monkeypatch, tmp_path) -> None:
    spawned = _patch_mpv(monkeypatch)
    sink = mpv_sink.MpvPipeWireSink(sock_path=tmp_path / "kurek_mpv.sock")
    assert sink.ensure_running() is True
    assert "--load-scripts=no" in spawned[0].cmd
    sink.shutdown()
