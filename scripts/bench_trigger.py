#!/usr/bin/env python3
"""
scripts/bench_trigger.py — Time compiled kurek-trigger against a mock UDS daemon.

Speaks the same line-oriented JSON protocol as kurek_daemon's UDS server.
Reports median and p95 wall times for N toggle round-trips.

Usage:
  python scripts/bench_trigger.py [--runs 200] [--trigger PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _find_trigger(explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.extend(
        [
            Path.home() / ".local" / "bin" / "kurek-trigger",
            ROOT / "bin" / "kurek-trigger",
            Path("/tmp/kurek-trigger-ci"),
        ]
    )
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c

    # Build from source into a temp binary for local/CI convenience.
    src = ROOT / "bin" / "kurek-trigger.c"
    out = Path(tempfile.gettempdir()) / "kurek-trigger-bench"
    cmd = ["gcc", "-O3", "-Wall", "-Wextra", str(src), "-o", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"Failed to build trigger:\n{proc.stderr}")
    out.chmod(0o755)
    return out


def _mock_server(sock_path: Path, stop: threading.Event) -> None:
    if sock_path.exists():
        sock_path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    os.chmod(sock_path, 0o600)
    server.listen(64)
    server.settimeout(0.2)
    try:
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            with conn:
                buf = b""
                conn.settimeout(1.0)
                try:
                    while b"\n" not in buf:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                except socket.timeout:
                    pass
                line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()
                try:
                    data = json.loads(line) if line else {}
                except json.JSONDecodeError:
                    data = {"action": line}
                action = data.get("action", "toggle")
                if action == "status":
                    conn.sendall(b'{"state":"idle"}\n')
                elif action == "subscribe":
                    conn.sendall(b'{"event":"state","state":"idle"}\n')
                else:
                    conn.sendall(b'{"ok":true,"state":"idle"}\n')
    finally:
        server.close()
        sock_path.unlink(missing_ok=True)


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=200)
    parser.add_argument("--trigger", default=None)
    parser.add_argument(
        "--sanity-ms",
        type=float,
        default=100.0,
        help="Soft sanity bound for p95 (warn only; exit 0 unless --strict).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if p95 exceeds --sanity-ms.",
    )
    args = parser.parse_args()

    trigger = _find_trigger(args.trigger)
    runtime = Path(tempfile.mkdtemp(prefix="kurek-bench-"))
    sock_path = runtime / "kurek.sock"
    stop = threading.Event()
    thread = threading.Thread(target=_mock_server, args=(sock_path, stop), daemon=True)
    thread.start()

    # Wait for socket
    deadline = time.time() + 3.0
    while time.time() < deadline and not sock_path.exists():
        time.sleep(0.01)
    if not sock_path.exists():
        stop.set()
        raise SystemExit("mock UDS server failed to start")

    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = str(runtime)
    # Avoid install_path daemon spawn on connect miss.
    env.pop("XDG_CONFIG_HOME", None)

    samples_ms: list[float] = []
    failures = 0
    for _ in range(args.runs):
        t0 = time.perf_counter()
        proc = subprocess.run(
            [str(trigger), "toggle"],
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        dt = (time.perf_counter() - t0) * 1000.0
        if proc.returncode != 0:
            failures += 1
            continue
        samples_ms.append(dt)

    stop.set()
    thread.join(timeout=2.0)

    if len(samples_ms) < max(5, args.runs // 10):
        print(
            f"[bench_trigger] FAIL: too many failures ({failures}/{args.runs})",
            file=sys.stderr,
        )
        return 1

    samples_ms.sort()
    median = statistics.median(samples_ms)
    p95 = _percentile(samples_ms, 95)
    mean = statistics.fmean(samples_ms)
    print(
        f"[bench_trigger] trigger={trigger} runs={len(samples_ms)} "
        f"failures={failures} median={median:.3f}ms p95={p95:.3f}ms "
        f"mean={mean:.3f}ms min={samples_ms[0]:.3f}ms max={samples_ms[-1]:.3f}ms"
    )

    if p95 > args.sanity_ms:
        print(
            f"[bench_trigger] WARN: p95 {p95:.3f}ms exceeds soft bound {args.sanity_ms:.1f}ms",
            file=sys.stderr,
        )
        if args.strict:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
