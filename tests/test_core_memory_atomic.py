"""memory/ writes are atomic and private (0600)."""
from __future__ import annotations

import json
import os
import stat

from memory.atomic_io import atomic_write_text


def test_atomic_write_creates_private_file(tmp_path) -> None:
    p = tmp_path / "long_term.json"
    atomic_write_text(p, json.dumps({"a": 1}))
    assert json.loads(p.read_text()) == {"a": 1}
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert [x.name for x in tmp_path.iterdir()] == ["long_term.json"]


def test_atomic_write_replaces_and_tightens(tmp_path) -> None:
    p = tmp_path / "api_keys.json"
    p.write_text("old")
    os.chmod(p, 0o644)
    atomic_write_text(p, "new")
    assert p.read_text() == "new"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_config_manager_patch_is_private(monkeypatch, tmp_path) -> None:
    import memory.config_manager as cm

    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cm, "CONFIG_FILE", tmp_path / "api_keys.json")
    cm.save_voice("sal")
    assert json.loads((tmp_path / "api_keys.json").read_text())["voice_name"]
    assert stat.S_IMODE(os.stat(tmp_path / "api_keys.json").st_mode) == 0o600
