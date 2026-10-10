"""
Optional per-query latency marks (ms deltas from query start).

Enable with KUREK_TIMING=1 (or true/yes/debug). Logs go to stdout as:
  [Kurek Timing] <mark>: <ms>ms
"""
from __future__ import annotations

import os
import threading
import time

_local = threading.local()


def timing_enabled() -> bool:
    flag = (os.environ.get("KUREK_TIMING") or "").strip().lower()
    return flag in ("1", "true", "yes", "on", "debug")


class QueryTiming:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self.marks: dict[str, float] = {}

    def mark(self, name: str, *, once: bool = False) -> None:
        if not timing_enabled():
            return
        if once and name in self.marks:
            return
        elapsed_ms = (time.perf_counter() - self.t0) * 1000.0
        self.marks[name] = elapsed_ms
        print(f"[Kurek Timing] {name}: {elapsed_ms:.1f}ms", flush=True)

    def summary(self) -> None:
        if not timing_enabled() or not self.marks:
            return
        parts = [f"{k}={v:.1f}ms" for k, v in self.marks.items()]
        print(f"[Kurek Timing] summary: {', '.join(parts)}", flush=True)


def begin_query() -> QueryTiming:
    qt = QueryTiming()
    _local.current = qt
    qt.mark("trigger_receipt")
    return qt


def current_timing() -> QueryTiming | None:
    return getattr(_local, "current", None)


def mark(name: str, *, once: bool = False) -> None:
    qt = current_timing()
    if qt is not None:
        qt.mark(name, once=once)


def end_query() -> None:
    qt = current_timing()
    if qt is not None:
        qt.summary()
    _local.current = None
