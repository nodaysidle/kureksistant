"""Installer unit templating and shell hygiene checks."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UNIT_TEMPLATE = ROOT / "desktop" / "kurek.service"
INSTALLER = ROOT / "install_linux.sh"


def test_unit_template_has_placeholders_not_hardcoded_paths() -> None:
    text = UNIT_TEMPLATE.read_text(encoding="utf-8")
    assert "@KUREK_DIR@" in text
    assert text.count("@KUREK_DIR@") >= 2
    assert "/home/arch" not in text
    assert "nodaysidle/kurekizmo" not in text
    assert "%h/dev/" not in text


def test_render_unit_sed_produces_no_hardcoded_arch_path(tmp_path: Path) -> None:
    install_dir = "/opt/example/kureksistant"
    dest = tmp_path / "kurek.service"
    # Mirror install_linux.sh render_unit exactly.
    with dest.open("w", encoding="utf-8") as fh:
        subprocess.run(
            [
                "sed",
                "-e",
                f"s|@KUREK_DIR@|{install_dir}|g",
                str(UNIT_TEMPLATE),
            ],
            check=True,
            stdout=fh,
        )
    rendered = dest.read_text(encoding="utf-8")
    assert "@KUREK_DIR@" not in rendered
    assert f"WorkingDirectory={install_dir}" in rendered
    assert f"ExecStart={install_dir}/.venv/bin/python -u {install_dir}/kurek_daemon.py" in rendered
    assert "/home/arch" not in rendered
    assert "MemoryHigh=512M" in rendered
    assert "MemoryMax=600M" in rendered


def test_installer_writes_install_path_and_renders_unit(tmp_path: Path) -> None:
    """Exercise templating + install_path + trigger build without network installs."""
    fake_root = tmp_path / "kureksistant"
    fake_root.mkdir()
    (fake_root / "desktop").mkdir()
    (fake_root / "bin").mkdir()
    shutil.copy(UNIT_TEMPLATE, fake_root / "desktop" / "kurek.service")
    shutil.copy(ROOT / "bin" / "kurek-trigger.c", fake_root / "bin" / "kurek-trigger.c")
    (fake_root / "bin" / "kurek").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    (fake_root / "desktop" / "kurek.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (fake_root / "desktop" / "kurek.desktop").write_text(
        "[Desktop Entry]\nExec=kurek toggle\n",
        encoding="utf-8",
    )
    (fake_root / "requirements.txt").write_text("# stub\n", encoding="utf-8")
    (fake_root / ".env.example").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")

    # Pre-create .venv with stub pip/python so the installer stays offline.
    venv_bin = fake_root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    python = venv_bin / "python"
    pip = venv_bin / "pip"
    python.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"playwright\" ]; then exit 0; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    pip.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    pip.chmod(0o755)

    home = tmp_path / "home"
    home.mkdir()
    config = home / ".config"
    config.mkdir()
    xdg_run = tmp_path / "xdg-run"
    xdg_run.mkdir()

    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    for name, body in {
        "pacman": "#!/bin/sh\nexit 0\n",
        "update-desktop-database": "#!/bin/sh\nexit 0\n",
        "systemctl": "#!/bin/sh\nexit 0\n",
    }.items():
        p = shim_dir / name
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)

    installer = fake_root / "install_linux.sh"
    shutil.copy(INSTALLER, installer)
    installer.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(config)
    env["XDG_RUNTIME_DIR"] = str(xdg_run)
    # Prefer shims; exclude uv so the pip path is used.
    path_parts = [str(shim_dir)]
    for part in env.get("PATH", "").split(":"):
        if part and "uv" not in part.lower():
            path_parts.append(part)
    env["PATH"] = ":".join(path_parts)
    env.pop("VIRTUAL_ENV", None)

    proc = subprocess.run(
        ["bash", str(installer)],
        cwd=str(fake_root),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"installer failed:\n{proc.stdout}\n{proc.stderr}"

    install_path_file = config / "kurek" / "install_path"
    assert install_path_file.is_file()
    assert install_path_file.read_text(encoding="utf-8").strip() == str(fake_root)

    unit = home / ".config" / "systemd" / "user" / "kurek.service"
    assert unit.is_file()
    unit_text = unit.read_text(encoding="utf-8")
    assert str(fake_root) in unit_text
    assert "@KUREK_DIR@" not in unit_text
    assert "/home/arch" not in unit_text

    trigger = home / ".local" / "bin" / "kurek-trigger"
    assert trigger.is_file()
    assert os.access(trigger, os.X_OK)

    # Idempotent re-run must not clobber an existing .env
    env_file = fake_root / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=keep-me\n", encoding="utf-8")
    marker = env_file.read_text(encoding="utf-8")
    proc2 = subprocess.run(
        ["bash", str(installer)],
        cwd=str(fake_root),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc2.returncode == 0, f"re-run failed:\n{proc2.stdout}\n{proc2.stderr}"
    assert env_file.read_text(encoding="utf-8") == marker


def test_shellcheck_install_linux() -> None:
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    proc = subprocess.run(
        ["shellcheck", "-x", str(INSTALLER)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
