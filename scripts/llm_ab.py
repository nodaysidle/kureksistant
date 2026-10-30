#!/usr/bin/env python3
"""Run Kurek's real prompt + tool loop on DeepSeek-Flash over a fixed set of prompts.

    .venv/bin/python scripts/llm_ab.py [--out FILE]

Read-only tools (web_search, weather, vault reads, system status) really run, so
search answers are real. Every other tool is recorded, not executed: nothing on the
desktop changes. History, daily log and memory triage are not written.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROMPTS = [
    ("web", "who won the last Formula 1 race?"),
    ("web", "what's the latest news about DeepSeek-V4.1-Flash?"),
    ("web", "kdaj igra Olimpija naslednjo tekmo?"),
    ("web", "is it going to rain in Ljubljana tomorrow?"),
    ("computer", "turn the volume down to 30 percent"),
    ("computer", "open firefox and go to github"),
    ("computer", "move this window to workspace 3"),
    ("computer", "what's on my screen right now?"),
    ("computer", "remind me in 20 minutes to check the oven"),
    ("understand", "uh can you like open the the music thing, spotify i mean"),
    ("understand", "close it"),
    ("understand", "delete notes.txt on my desktop"),
    ("understand", "how's my cpu doing"),
    ("understand", "what should I launch next from my portfolio?"),
    ("understand", "write a commit message for: fixed the crash when the mic gets unplugged"),
]

READ_ONLY = {
    "web_search": None,
    "weather_report": None,
    "vault_knowledge": {"read", "search", "list_projects"},
    "system_monitor": {"status"},
    "manage_memory": {"recall"},
}


def build_engine():
    import kurek_daemon as daemon

    daemon._resolve_input_device = lambda: None
    daemon.get_deepgram_key = lambda: None
    daemon.get_xai_key = lambda: None
    daemon.WhisperSTT = lambda **_k: None
    daemon.auto_screen_enabled = lambda: False

    class _SilentTTS:
        is_playing = False

        def speak(self, text): pass
        def speak_chunk(self, text, append=False): pass
        def begin_stream(self): pass
        def enqueue(self, text, play_local=True, on_audio=None): pass
        def wait_stream(self, timeout=60.0): return True
        def synthesize(self, text): return None
        def stop(self): pass

    daemon.create_tts_player = lambda *_a, **_k: _SilentTTS()
    daemon.get_mpv_sink = lambda: SimpleNamespace(ensure_running=lambda: None)
    daemon.get_hyprland_watcher = lambda: None
    for name in ("_report_startup_problems", "_hourly_consolidation_loop", "_notify_state",
                 "_save_history", "_append_daily_memory", "_consolidate_with_jev"):
        setattr(daemon.KurekEngine, name, lambda self, *_a, **_k: None)

    engine = daemon.KurekEngine()
    engine.stt = None
    real_run = engine.actions.run

    def guarded_run(name, params, *a, **k):
        engine.calls.append(f"{name}({json.dumps(params, ensure_ascii=False)})")
        allowed = READ_ONLY.get(name, False)
        if allowed is None or (allowed and (params or {}).get("action") in allowed):
            return real_run(name, params, *a, **k)
        return f"{name} accepted (test mode)."

    engine.actions.run = guarded_run
    engine.calls = []
    return engine


def run_one(engine, prompt: str) -> dict:
    engine.history = []
    engine.calls = []
    engine._cancel_requested = False
    t0 = time.monotonic()
    rec: dict = {"tools": [], "first_sentence_s": None, "reply": ""}

    def cb(ev):
        if ev.get("type") == "sentence" and rec["first_sentence_s"] is None:
            rec["first_sentence_s"] = round(time.monotonic() - t0, 2)
        elif ev.get("type") == "done":
            rec["reply"] = ev.get("reply", "")

    engine._handle_text_query_inner(prompt, callback=cb, mute_speaker=True)
    rec["tools"] = list(engine.calls)
    rec["total_s"] = round(time.monotonic() - t0, 2)
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="llm_ab.json")
    args = ap.parse_args()

    engine = build_engine()
    results = []
    for kind, prompt in PROMPTS:
        rec = run_one(engine, prompt)
        print(f"{prompt[:40]:40} {rec['total_s']:5}s  {rec['tools']}  -> {rec['reply'][:80]}", flush=True)
        results.append({"kind": kind, "prompt": prompt, **rec})
    Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
