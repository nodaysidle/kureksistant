"""
Optional per-query latency marks (ms deltas from query start).

Enable with KUREK_TIMING=1 (or true/yes/debug). Logs go to stdout as:
  [Kurek Timing] <mark>: <ms>ms
Marks: trigger_receipt, stt_done, deepseek_request_sent, deepseek_headers,
deepseek_first_reasoning, first_deepseek_token, first_deepseek_sentence,
tool_start:<name>, tool_end:<name>, first_speak_chunk, tts_synth_done,
mpv_loadfile and summary; a voice turn's clock starts at the stop click.
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
    def __init__(self, t0: float | None = None) -> None:
        self.t0 = time.perf_counter() if t0 is None else t0
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
        parts = [f"{k}={v:.1f}ms" for k, v in list(self.marks.items())]  # the TTS worker may still be marking
        print(f"[Kurek Timing] summary: {', '.join(parts)}", flush=True)


def begin_query(t0: float | None = None) -> QueryTiming:
    """Start this thread's clock; t0 (a time.perf_counter() value) backdates it, e.g. to a voice turn's stop click."""
    qt = QueryTiming(t0)
    _local.current = qt
    qt.mark("trigger_receipt")
    return qt


def current_timing() -> QueryTiming | None:
    return getattr(_local, "current", None)


def set_current(qt: QueryTiming | None) -> None:
    """Let a worker thread mark into a query's clock (the TTS worker, per sentence); None clears it."""
    _local.current = qt


def mark(name: str, *, once: bool = False) -> None:
    qt = current_timing()
    if qt is not None:
        qt.mark(name, once=once)


def end_query() -> None:
    qt = current_timing()
    if qt is not None:
        qt.summary()
    _local.current = None
