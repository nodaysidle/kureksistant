"""
System Monitor — background metric checks with voice alert support.
Zero subprocess calls on all platforms — uses ctypes/pynvml/psutil/wmi only.
"""
import ctypes
import platform
import time

import psutil

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

DEFAULT_THRESHOLDS = {
    "cpu":  90.0,
    "ram":  90.0,
    "temp": 85.0,
    "gpu":  95.0,
}

_COOLDOWN   = 300
_CPU_STREAK = 3

# ── NVML DLL cache (Windows: nvml.dll, Linux: libnvidia-ml.so.1) ─────────────
_nvml_lib: object = None
_nvml_ok:  object = None   # None=untested  True=works  False=unavailable


def _nvml_gpu() -> float:
    """GPU utilisation via NVML — zero subprocess on all platforms."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            candidates = (
                "libnvidia-ml.so.1",
                "libnvidia-ml.so",
                "libnvidia-ml.dylib",
            )
            _load = ctypes.CDLL
            for name in candidates:
                try:
                    lib = _load(name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            _nvml_ok = False
            return -1.0

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        u = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
        _nvml_ok = True
        return float(u.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


def _get_gpu_usage() -> float:
    # pynvml — subprocess-free, works everywhere if installed
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
    except Exception:
        pass

    return _nvml_gpu()


def _get_cpu_temp() -> float:
    # psutil — works on Linux; occasionally Windows with proper drivers
    try:
        temps = psutil.sensors_temperatures()
        for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                     "cpu-thermal", "zenpower", "it8688"]:
            if name in temps and temps[name]:
                return temps[name][0].current
        for entries in temps.values():
            if entries:
                return entries[0].current
    except Exception:
        pass

    return -1.0


def get_system_status() -> dict:
    """Snapshot of current system metrics for the system_status tool."""
    cpu  = psutil.cpu_percent(interval=0.2)
    ram  = psutil.virtual_memory()
    temp = _get_cpu_temp()
    gpu  = _get_gpu_usage()

    boot_time   = psutil.boot_time()
    uptime_secs = time.time() - boot_time
    uptime_h    = int(uptime_secs // 3600)
    uptime_m    = int((uptime_secs % 3600) // 60)

    return {
        "cpu_percent":   round(cpu, 1),
        "ram_percent":   round(ram.percent, 1),
        "ram_used_gb":   round(ram.used   / 1024 ** 3, 1),
        "ram_total_gb":  round(ram.total  / 1024 ** 3, 1),
        "cpu_temp_c":    round(temp, 1) if temp > 0 else None,
        "gpu_percent":   round(gpu,  1) if gpu  >= 0 else None,
        "uptime":        f"{uptime_h}h {uptime_m}m",
        "process_count": len(psutil.pids()),
    }


class SystemMonitor:
    """
    Stateful monitor — cooldown state persists across session reconnections.
    Call check() periodically; returns a [SYSTEM_ALERT] string or None.
    """

    def __init__(self, thresholds: dict | None = None):
        self.thresholds   = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._last_alert: dict[str, float] = {}
        self._cpu_streak  = 0

    def _can_alert(self, key: str) -> bool:
        return (time.monotonic() - self._last_alert.get(key, 0)) > _COOLDOWN

    def _record(self, key: str):
        self._last_alert[key] = time.monotonic()

    def check(self) -> str | None:
        try:
            cpu  = psutil.cpu_percent(interval=None)
            ram  = psutil.virtual_memory().percent
            temp = _get_cpu_temp()
            gpu  = _get_gpu_usage()
        except Exception:
            return None

        alerts: list[str] = []

        if cpu >= self.thresholds["cpu"]:
            self._cpu_streak += 1
            if self._cpu_streak >= _CPU_STREAK and self._can_alert("cpu"):
                alerts.append(
                    f"[SYSTEM_ALERT] CPU usage has been critically high ({cpu:.0f}%) "
                    "for several seconds. Warn the user in their language and suggest "
                    "closing heavy applications."
                )
                self._record("cpu")
                self._cpu_streak = 0
        else:
            self._cpu_streak = 0

        if ram >= self.thresholds["ram"] and self._can_alert("ram"):
            alerts.append(
                f"[SYSTEM_ALERT] RAM is at {ram:.0f}% — nearly exhausted. "
                "Warn the user in their language and suggest freeing memory."
            )
            self._record("ram")

        if temp > 0 and temp >= self.thresholds["temp"] and self._can_alert("temp"):
            alerts.append(
                f"[SYSTEM_ALERT] CPU temperature is {temp:.0f}°C — above the safe limit. "
                "Warn the user in their language and advise reducing system load "
                "or checking cooling."
            )
            self._record("temp")

        if gpu >= 0 and gpu >= self.thresholds["gpu"] and self._can_alert("gpu"):
            alerts.append(
                f"[SYSTEM_ALERT] GPU load is at {gpu:.0f}%. "
                "Briefly inform the user in their language."
            )
            self._record("gpu")

        return " ".join(alerts) if alerts else None


# ── Sustained 300-Second Window Watcher ─────────────────────────────────────────

from collections import deque
import threading
import shutil
import subprocess

class SustainedResourceWatcher:
    """
    Watches CPU and RAM usage over a sustained 300-second (5 minute) window.
    If the average over 300s exceeds thresholds, dispatches desktop notifications
    and invokes the optional alert callback (e.g. spoken TTS).
    """

    def __init__(
        self,
        window_seconds: int = 300,
        sample_interval: int = 5,
        cpu_threshold: float = 85.0,
        ram_threshold: float = 85.0,
        alert_cooldown: int = 600,
    ):
        self.window_seconds = window_seconds
        self.sample_interval = sample_interval
        self.cpu_threshold = cpu_threshold
        self.ram_threshold = ram_threshold
        self.alert_cooldown = alert_cooldown

        self.max_samples = max(10, window_seconds // sample_interval)
        self.history: deque[tuple[float, float, float]] = deque(maxlen=self.max_samples)  # (timestamp, cpu, ram)
        self.lock = threading.Lock()

        self._running = False
        self._thread: threading.Thread | None = None
        self._last_alert_time: dict[str, float] = {}
        self.alert_callback = None

    def record_sample(self):
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            now = time.time()
            with self.lock:
                self.history.append((now, cpu, ram))
        except Exception:
            pass

    def get_stats(self) -> dict:
        with self.lock:
            if not self.history:
                return {"avg_cpu": 0.0, "avg_ram": 0.0, "samples": 0}
            cpus = [h[1] for h in self.history]
            rams = [h[2] for h in self.history]
            return {
                "avg_cpu": round(sum(cpus) / len(cpus), 1),
                "avg_ram": round(sum(rams) / len(rams), 1),
                "samples": len(self.history),
                "window_coverage_sec": len(self.history) * self.sample_interval,
            }

    def _get_top_culprit(self, metric: str = "cpu") -> str:
        procs = []
        for p in psutil.process_iter(["name", "cpu_percent", "memory_percent"]):
            try:
                info = p.info
                procs.append(info)
            except Exception:
                pass

        if metric == "cpu":
            procs.sort(key=lambda x: (x.get("cpu_percent") or 0), reverse=True)
        else:
            procs.sort(key=lambda x: (x.get("memory_percent") or 0), reverse=True)

        if procs and procs[0].get("name"):
            name = procs[0]["name"]
            # Filter out generic daemon/system names if possible
            for pr in procs[:5]:
                pname = pr.get("name", "")
                if pname and pname not in ("python", "psutil", "systemd"):
                    return pname
            return name
        return "background processes"

    def _notify_desktop(self, title: str, body: str):
        if shutil.which("notify-send"):
            try:
                subprocess.run(["notify-send", "-u", "critical", "-a", "Kurek", title, body], check=False, timeout=10)
            except Exception:
                pass

    def _loop(self):
        # Warm up psutil
        psutil.cpu_percent(interval=None)
        while self._running:
            time.sleep(self.sample_interval)
            self.record_sample()

            stats = self.get_stats()
            # Require at least 25 samples (~125s) before asserting sustained threshold
            if stats["samples"] >= min(25, self.max_samples // 2):
                now = time.time()

                # Check Sustained CPU
                if stats["avg_cpu"] >= self.cpu_threshold:
                    last_alert = self._last_alert_time.get("cpu", 0)
                    if (now - last_alert) >= self.alert_cooldown:
                        self._last_alert_time["cpu"] = now
                        top_proc = self._get_top_culprit("cpu")
                        alert_msg = (
                            f"Notice: Your CPU usage has averaged {stats['avg_cpu']:.0f}% over the last 5 minutes, "
                            f"driven mostly by {top_proc}."
                        )
                        print(f"[Kurek System Alert] {alert_msg}", flush=True)
                        self._notify_desktop("High CPU Load (5m Average)", f"Average: {stats['avg_cpu']}%\nTop process: {top_proc}")
                        if self.alert_callback:
                            try:
                                self.alert_callback(alert_msg)
                            except Exception as e:
                                print(f"[Kurek System Alert] Callback notice: {e}", flush=True)

                # Check Sustained RAM
                if stats["avg_ram"] >= self.ram_threshold:
                    last_alert = self._last_alert_time.get("ram", 0)
                    if (now - last_alert) >= self.alert_cooldown:
                        self._last_alert_time["ram"] = now
                        top_proc = self._get_top_culprit("ram")
                        alert_msg = (
                            f"Notice: Your memory usage has averaged {stats['avg_ram']:.0f}% over the last 5 minutes, "
                            f"primarily consumed by {top_proc}."
                        )
                        print(f"[Kurek System Alert] {alert_msg}", flush=True)
                        self._notify_desktop("High RAM Load (5m Average)", f"Average: {stats['avg_ram']}%\nTop process: {top_proc}")
                        if self.alert_callback:
                            try:
                                self.alert_callback(alert_msg)
                            except Exception as e:
                                print(f"[Kurek System Alert] Callback notice: {e}", flush=True)

    def start(self, alert_callback=None):
        if self._running:
            return
        self.alert_callback = alert_callback
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="SustainedResourceWatcher")
        self._thread.start()
        print("[Kurek Resource Watcher] 300-second sustained CPU/RAM watcher started.", flush=True)

    def stop(self):
        self._running = False


_GLOBAL_WATCHER = SustainedResourceWatcher()
_GLOBAL_WATCHER.start()


def system_monitor(parameters: dict, player=None, session_memory=None) -> str:
    """Action handler for checking system health and 5-minute averages."""
    params = parameters or {}
    action = params.get("action", "status").lower().strip()

    if action in ("status", "check", "metrics", "health"):
        cur = get_system_status()
        five_min = _GLOBAL_WATCHER.get_stats()

        avg_str = ""
        if five_min["samples"] >= 5:
            avg_str = f" Over the last 5 minutes, CPU averaged {five_min['avg_cpu']}% and RAM averaged {five_min['avg_ram']}%."

        gpu_str = f", GPU at {cur['gpu_percent']}%" if cur.get("gpu_percent") is not None else ""
        temp_str = f", CPU temp {cur['cpu_temp_c']}°C" if cur.get("cpu_temp_c") is not None else ""

        return (
            f"System Status: CPU currently at {cur['cpu_percent']}%, RAM at {cur['ram_percent']}% "
            f"({cur['ram_used_gb']} GB of {cur['ram_total_gb']} GB used){gpu_str}{temp_str}.{avg_str}"
        )

    elif action in ("set_threshold", "config"):
        cpu_t = params.get("cpu_threshold")
        ram_t = params.get("ram_threshold")
        if cpu_t:
            _GLOBAL_WATCHER.cpu_threshold = float(cpu_t)
        if ram_t:
            _GLOBAL_WATCHER.ram_threshold = float(ram_t)
        return f"Resource thresholds updated: CPU { _GLOBAL_WATCHER.cpu_threshold}%, RAM {_GLOBAL_WATCHER.ram_threshold}%."

    return "Available actions: status, set_threshold."


TOOL = {
    "name": "system_monitor",
    "description": (
        "Real-time hardware performance monitor and 5-minute sustained resource analyzer. "
        "Use when the user asks about CPU, RAM, GPU, temperature, system health, or resource bottlenecks."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "status | set_threshold"
            },
            "cpu_threshold": {
                "type": "NUMBER",
                "description": "Optional sustained CPU % threshold (e.g. 85.0)"
            },
            "ram_threshold": {
                "type": "NUMBER",
                "description": "Optional sustained RAM % threshold (e.g. 85.0)"
            }
        },
        "required": ["action"]
    },
    "handler": system_monitor
}

