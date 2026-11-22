"""system_monitor status states installed RAM and splits swap (cannot read as RAM)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import actions.system_monitor as sm

GIB = 1024**3
BLOCK = 0x8000000  # 128 MiB


def _sysfs(root: Path, online: int, offline: int = 0, block: int = BLOCK) -> Path:
    root.mkdir(parents=True)
    (root / "block_size_bytes").write_text(f"{block:x}\n")
    for i in range(online + offline):
        d = root / f"memory{i}"
        d.mkdir()
        (d / "state").write_text("online\n" if i < online else "offline\n")
    return root


def _swaps(path: Path, *lines: str) -> Path:
    header = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
    path.write_text(header + "".join(line + "\n" for line in lines))
    return path


ZRAM = "/dev/zram0\t\t\t\tpartition\t15624806\t10905190\t100"
DISK = "/swap/swapfile\t\t\t\tfile\t\t15624806\t104858\t-2"


@pytest.fixture
def status(monkeypatch, tmp_path):
    """Run system_monitor('status') against a fake machine; returns a runner."""

    def run(sysfs: Path | None, swaps: Path | None) -> str:
        vm = SimpleNamespace(
            percent=76.3,
            used=int(11.4 * GIB),
            total=int(14.92 * GIB),
            available=int(3.5 * GIB),
        )
        monkeypatch.setattr(sm.psutil, "cpu_percent", lambda interval=None: 2.5)
        monkeypatch.setattr(sm.psutil, "virtual_memory", lambda: vm)
        monkeypatch.setattr(sm.psutil, "boot_time", lambda: 0.0)
        monkeypatch.setattr(sm.psutil, "pids", lambda: [1, 2])
        monkeypatch.setattr(sm, "_get_cpu_temp", lambda: 49.0)
        monkeypatch.setattr(sm, "_get_gpu_usage", lambda: -1.0)
        stats = {"samples": 9, "avg_cpu": 6.5, "avg_ram": 76.4}
        monkeypatch.setattr(
            sm, "_GLOBAL_WATCHER", SimpleNamespace(get_stats=lambda: stats)
        )
        real_installed, real_swaps = sm._installed_ram_gb, sm._read_swaps
        missing = tmp_path / "missing"
        monkeypatch.setattr(
            sm, "_installed_ram_gb", lambda total: real_installed(total, sysfs or missing)
        )
        monkeypatch.setattr(
            sm, "_read_swaps", lambda: real_swaps(swaps or missing)
        )
        return sm.system_monitor({"action": "status"})

    return run


def test_owner_machine_exact_status(status, tmp_path):
    out = status(_sysfs(tmp_path / "mem", 124), _swaps(tmp_path / "swaps", ZRAM, DISK))
    assert out == (
        "System Status: CPU currently at 2.5%, RAM at 76.3% "
        "(11.4 GB of 14.9 GB usable used, 3.5 GB available; 16 GB installed), "
        "CPU temp 49.0°C. Over the last 5 minutes, CPU averaged 6.5% and RAM "
        "averaged 76.4%. Compressed swap in RAM (zram): 10.4 GB of 14.9 GB. "
        "Swap on disk: 0.1 GB of 14.9 GB. "
        "Swap is not RAM: this machine's memory is the 16 GB installed."
    )
    assert "29" not in out


def test_no_swap_file_adds_no_swap_sentence(status, tmp_path):
    out = status(_sysfs(tmp_path / "mem", 124), None)
    assert "swap" not in out.lower()
    assert "16 GB installed)" in out


def test_missing_sysfs_drops_installed_clause_and_says_usable(status, tmp_path):
    out = status(None, _swaps(tmp_path / "swaps", ZRAM))
    assert "installed" not in out
    assert "3.5 GB available)" in out
    assert out.endswith("Swap is not RAM: this machine's memory is the 14.9 GB usable.")


def test_two_disk_areas_are_summed(status, tmp_path):
    two = "/swap/second\t\t\t\tfile\t\t15624806\t104858\t-3"
    out = status(None, _swaps(tmp_path / "swaps", DISK, two))
    assert out.count("Swap on disk:") == 1
    assert "Swap on disk: 0.2 GB of 29.8 GB." in out


def test_offline_block_not_counted(tmp_path):
    root = _sysfs(tmp_path / "mem", 100, offline=24)  # 12.5 GiB online
    assert sm._installed_ram_gb(11 * GIB, root) == 13
    assert sm._installed_ram_gb(int(14.9 * GIB), root) is None  # not larger


def test_malformed_swap_line_skipped(tmp_path):
    p = _swaps(tmp_path / "swaps", "garbage", "/dev/zram0 partition x y 1", ZRAM)
    areas = sm._read_swaps(p)
    assert [a["name"] for a in areas] == ["/dev/zram0"]
    assert areas[0]["kind"] == "zram"
    assert sm._read_swaps(tmp_path / "nope") == []


def test_existing_keys_and_types_kept(monkeypatch):
    s = sm.get_system_status()
    expected = {
        "cpu_percent": float,
        "ram_percent": float,
        "ram_used_gb": float,
        "ram_total_gb": float,
        "uptime": str,
        "process_count": int,
    }
    for key, typ in expected.items():
        assert isinstance(s[key], typ), key
    assert s["cpu_temp_c"] is None or isinstance(s["cpu_temp_c"], float)
    assert s["gpu_percent"] is None or isinstance(s["gpu_percent"], float)
    assert isinstance(s["ram_available_gb"], float)
    assert s["ram_installed_gb"] is None or isinstance(s["ram_installed_gb"], int)
    assert isinstance(s["swap"], list)
