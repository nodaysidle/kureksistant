#!/usr/bin/env python3
"""
KUREK DAEMON — Lean headless personal AI assistant for Arch Linux / Hyprland.
Background service with observed ~450MB peak RAM when faster-whisper is loaded.

Connects with:
  • Linux UDS trigger (kurek-trigger) / Hyprland Middle Click
  • Android remote app over Tailscale (/remote/prompt, SSE)
  • Brain: DeepSeek API (deepseek-flash, served by DeepSeek-V4.1-Flash)
  • STT: OpenRouter Whisper / Deepgram Nova-2, local faster-whisper fallback
  • xAI Grok TTS (Sol) via persistent mpv PipeWire sink
"""
import base64
import hmac
import ipaddress
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# PortAudio (sounddevice) dlopen()s a native library, while numpy starts an
# OpenBLAS thread pool (one thread per core) at import. If the threads exist
# first, glibc's loader lock races them and the process deadlocks at import --
# measured 7/30 starts hung forever, which under Type=simple looks like a
# healthy service that never binds. Import PortAudio before any thread pool.
import sounddevice as sd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import socket
from memory.config_manager import (
    get_deepgram_key,
    get_deepseek_key,
    get_openrouter_api_key,
    get_user_name,
    get_xai_key,
    load_api_keys,
    get_remote_token,
    resolve_hermes_memory_dirs,
)
from core.decision_client import (
    SCREEN_CONTEXT_THRESHOLD,
    auto_screen_enabled,
    query_needs_screen,
)
from core.llm_client import brain_name, stream_deepseek_sentences
from core.live_voice import live_voice_enabled, start_live_servers
from core.tts import create_tts_player
from core.stt import DeepgramSTT, FallbackSTT, OpenRouterSTT, STTError, WhisperSTT
from core.action_loader import discover_actions
from core.hyprland_watcher import get_hyprland_watcher
from core.mpv_sink import get_mpv_sink
from core.timing import begin_query, end_query
from core import confirm
from core.proposals import propose
from core.rules import pinned_block as rules_pinned_block
from core.rules import prompt_block as rules_prompt_block
from memory import ledger
from memory.atomic_io import atomic_write_text

# Audio recording configuration
SAMPLE_RATE = 16000
CHANNELS = 1
SILENCE_THRESHOLD = 0.003  # Sensitive threshold for USB/desk mics
VAD_WARMUP_S = float(os.environ.get("KUREK_VAD_WARMUP_S", "0.3"))  # fast room calibration at the start
SILENCE_DURATION = float(os.environ.get("KUREK_SILENCE_DURATION", "4.0"))   # Base pause after speech before auto-submit
SILENCE_GROWTH = float(os.environ.get("KUREK_SILENCE_GROWTH", "0.6"))       # Extra pause granted per second already spoken
SILENCE_MAX_EXTRA = float(os.environ.get("KUREK_SILENCE_MAX_EXTRA", "10.0")) # Cap on that adaptive extra pause
MAX_RECORD_SECONDS = float(os.environ.get("KUREK_MAX_RECORD_SECONDS", "180.0")) # Hard ceiling for one dictation
INITIAL_SILENCE_TIMEOUT = float(os.environ.get("KUREK_INITIAL_SILENCE_TIMEOUT", "30.0")) # Give up if no speech at all

# Upper bound on each memory markdown file injected into the system prompt.
MEMORY_FILE_MAX_CHARS = int(os.environ.get("KUREK_MEMORY_FILE_MAX_CHARS", "6000"))


def vad_update(
    noise_floor: float,
    speech_chunks: int,
    has_speech: bool,
    rms: float,
    *,
    warmup: bool = False,
) -> tuple[float, int, bool, bool]:
    """One step of the mic voice-activity detector.

    Returns (noise_floor, speech_chunks, has_speech, sound_now). The ambient
    noise floor adapts until speech starts; two loud chunks confirm speech;
    after that, anything above the softer silence threshold counts as sound.

    Both gates are relative to the measured room, not to a fixed level: a quiet
    desk mic (ambient RMS ~0.001) sits far below the old 0.045 floor, so its
    owner was never detected as speaking at all.
    """
    if not has_speech:
        # During the first fraction of a second the floor learns the room fast,
        # so a loud fan is not mistaken for the speaker and a silent desk mic is
        # not held to a level it can never reach.
        alpha = 0.5 if warmup else 0.15
        noise_floor = (1.0 - alpha) * noise_floor + alpha * rms
    speech_threshold = max(0.006, noise_floor * 2.0)
    silence_threshold = max(0.0025, noise_floor * 1.3)
    sound_now = False
    if rms > speech_threshold:
        speech_chunks += 1
        if speech_chunks >= 2:
            has_speech = True
            sound_now = True
    elif has_speech and rms > silence_threshold:
        sound_now = True
    return noise_floor, speech_chunks, has_speech, sound_now


def auto_submit_enabled() -> bool:
    """False when ``KUREK_AUTO_SUBMIT=off``: only a click (or the ceiling) stops.

    Long questions are the common failure: the speaker pauses to compose, the
    watchdog decides the thought ended, and the answer lands mid-sentence. With
    auto-submit off, the second middle-click is the only "done" signal and the
    ceiling stays as the last-resort bound.
    """
    flag = (os.environ.get("KUREK_AUTO_SUBMIT") or "adaptive").strip().lower()
    return flag not in ("0", "false", "no", "off", "disabled", "manual")


def effective_silence_window(last_sound_time: float, record_start_time: float) -> float:
    """How long a pause the speaker gets before auto-submit.

    Mid-thought pauses grow with how much has already been said: a two-second
    answer keeps the base window, while a long question earns up to
    ``SILENCE_MAX_EXTRA`` more. The second middle-click still stops immediately,
    so this only widens the safety net (and ``KUREK_AUTO_SUBMIT=off`` removes it).
    """
    spoken_span = max(0.0, last_sound_time - record_start_time)
    extra = min(SILENCE_MAX_EXTRA, spoken_span * SILENCE_GROWTH)
    return SILENCE_DURATION + extra


def silence_stop_reason(now: float, has_speech: bool, last_sound_time: float, record_start_time: float) -> str | None:
    """Why the watchdog should stop recording now, or None to keep listening.

    ``KUREK_AUTO_SUBMIT=off`` means the second click is the only "done" signal:
    neither the pause rule nor the no-speech rule may end a session. The ceiling
    still bounds a forgotten microphone.
    """
    if auto_submit_enabled():
        if has_speech and now - last_sound_time > effective_silence_window(last_sound_time, record_start_time):
            return "silence"
        if not has_speech and now - record_start_time > INITIAL_SILENCE_TIMEOUT:
            return "no_speech"
    if record_start_time > 0 and now - record_start_time > MAX_RECORD_SECONDS:
        return "max_duration"
    return None


_warned_truncated: set[tuple[Path, int]] = set()


def brain_key_problem() -> str | None:
    """Why the brain cannot answer, or None when its API key is present."""
    if not get_deepseek_key():
        return "DEEPSEEK_API_KEY missing — Kurek cannot answer."
    return None


def read_capped(path: Path, limit: int = MEMORY_FILE_MAX_CHARS) -> str:
    """Read a markdown memory file, keeping its head and newest tail within limit."""
    text = path.read_text(encoding="utf-8").strip()
    if len(text) <= limit:
        return text
    if (path, len(text)) not in _warned_truncated:
        _warned_truncated.add((path, len(text)))
        print(f"[Kurek Memory] WARNING: {path} is {len(text)} chars, over the {limit} cap; "
              f"the middle is hidden from the model. Raise KUREK_MEMORY_FILE_MAX_CHARS.", flush=True)
    head = limit // 3
    tail = limit - head
    return f"{text[:head]}\n[… older entries omitted …]\n{text[-tail:]}"



def _run_tool_prompt_line() -> str:
    """System-prompt line for the `run` tool. Never advertises arbitrary
    command execution unless KUREK_REMOTE_ALLOW_EXEC=1 is set."""
    try:
        from actions.run import is_unrestricted_shell
        unrestricted = is_unrestricted_shell()
    except Exception:
        unrestricted = False
    if unrestricted:
        return (
            "• run: ⚠️ UNRESTRICTED SHELL ENABLED (KUREK_REMOTE_ALLOW_EXEC=1). Can execute arbitrary shell "
            "commands as the user via action='exec' with cmd='...'. Prefer read-only commands and confirm "
            "before anything destructive. "
        )
    return (
        "• run: sandboxed, auditable runner for allowlisted read-only inspection and build/test commands "
        "(action='exec' with cmd='...'). Destructive commands are blocked. "
    )


# HTTP remote-access hardening
HTTP_MAX_BODY_BYTES = 64 * 1024
REMOTE_TOKEN_MIN_LEN = 32
# Tools that execute model-generated code or shell commands — blocked for remote
# clients unless KUREK_REMOTE_ALLOW_EXEC=1.
REMOTE_BLOCKED_EXEC_TOOLS = frozenset({
    "desktop_control",
    "code_helper",
    "dev_agent",
    "run",
    # codebase reads arbitrary files under the project roots; keep it local-only.
    "codebase",
})


def remote_allow_exec() -> bool:
    flag = (os.environ.get("KUREK_REMOTE_ALLOW_EXEC") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def remote_token_is_strong(token: str | None) -> bool:
    return bool(token) and len(token) >= REMOTE_TOKEN_MIN_LEN


def parse_bearer_token(authorization: str | None) -> str | None:
    """Strict ``Authorization: Bearer <token>`` parse (single space, case-sensitive)."""
    if authorization is None:
        return None
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return None
    token = authorization[len(prefix):]
    if not token or token != token.strip() or any(ch.isspace() for ch in token):
        return None
    return token


def tokens_match(provided: str, expected: str) -> bool:
    try:
        return hmac.compare_digest(provided, expected)
    except (TypeError, ValueError):
        return False


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    h = host.strip().lower()
    if h in ("localhost", "::1"):
        return True
    try:
        return ipaddress.ip_address(h.split("%")[0]).is_loopback
    except ValueError:
        return h.startswith("127.")


def is_tailscale_cgnat_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version != 4:
        return False
    return ip in ipaddress.ip_network("100.64.0.0/10")


def detect_tailscale_ipv4() -> str | None:
    """Best-effort Tailscale IPv4: CLI first, then tailscale0 addresses."""
    try:
        proc = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                cand = line.strip()
                if cand and is_tailscale_cgnat_ip(cand):
                    return cand
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    try:
        proc = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", "tailscale0"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if proc.returncode == 0:
            for part in proc.stdout.replace("/", " ").split():
                if is_tailscale_cgnat_ip(part):
                    return part
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def require_strong_remote_token_for_bind() -> str:
    token = get_remote_token()
    if not remote_token_is_strong(token):
        raise RuntimeError(
            "Remote HTTP bind refused: set KUREK_REMOTE_TOKEN to a secret of at least "
            f"{REMOTE_TOKEN_MIN_LEN} characters (e.g. openssl rand -hex 32)."
        )
    return token  # type: ignore[return-value]


def resolve_http_bind_hosts() -> tuple[list[str], bool]:
    """
    Return (hosts_to_bind_now, watch_tailscale).

    Default is loopback only. Remote bind is opt-in via KUREK_REMOTE_BIND=tailscale
    or an explicit KUREK_BIND address. Never binds 0.0.0.0 unless explicitly set.
    """
    explicit = (os.environ.get("KUREK_BIND") or "").strip()
    remote_mode = (os.environ.get("KUREK_REMOTE_BIND") or "").strip().lower()

    if explicit:
        if explicit in ("0.0.0.0", "::"):
            require_strong_remote_token_for_bind()
            print(
                "[Kurek Server] ⚠️  SECURITY WARNING: binding HTTP to "
                f"{explicit} exposes the daemon on ALL interfaces. "
                "Prefer KUREK_REMOTE_BIND=tailscale.",
                flush=True,
            )
            return [explicit], False
        if is_loopback_host(explicit):
            return [explicit], False
        require_strong_remote_token_for_bind()
        hosts = ["127.0.0.1"]
        if explicit not in hosts:
            hosts.append(explicit)
        return hosts, False

    hosts = ["127.0.0.1"]
    if remote_mode in ("", "off", "0", "false", "no", "loopback", "localhost"):
        return hosts, False

    if remote_mode == "tailscale":
        require_strong_remote_token_for_bind()
        ts_ip = detect_tailscale_ipv4()
        if ts_ip:
            hosts.append(ts_ip)
            return hosts, True
        print(
            "[Kurek Server] KUREK_REMOTE_BIND=tailscale but Tailscale IPv4 not found yet "
            "— listening on 127.0.0.1 only; will retry when Tailscale comes up.",
            flush=True,
        )
        return hosts, True

    # Treat unknown KUREK_REMOTE_BIND values as an explicit host address.
    require_strong_remote_token_for_bind()
    if remote_mode not in hosts:
        hosts.append(remote_mode)
    return hosts, False


def _normalize_json_schema(obj):
    """Ensure JSON schema types are lowercase for standard OpenAI/DeepSeek schema validation."""
    if isinstance(obj, dict):
        new_dict = {}
        for k, v in obj.items():
            if k == "type" and isinstance(v, str):
                new_dict[k] = v.lower()
            else:
                new_dict[k] = _normalize_json_schema(v)
        return new_dict
    elif isinstance(obj, list):
        return [_normalize_json_schema(x) for x in obj]
    return obj


def _resolve_input_device():
    """Find input device index matching user preference or default with sample rate compatibility."""
    from memory.config_manager import get_input_device
    preferred = (get_input_device() or "").lower()
    devices = sd.query_devices()

    # 1. Try preferred hardware device if configured and it supports 16kHz
    if preferred:
        for idx, d in enumerate(devices):
            dname = d.get("name", "")
            if d.get("max_input_channels", 0) > 0 and preferred in dname.lower():
                try:
                    sd.check_input_settings(device=idx, samplerate=SAMPLE_RATE, channels=CHANNELS)
                    print(f"[Kurek Mic] Using direct hardware device [{idx}]: {dname}")
                    return idx
                except Exception:
                    print(f"[Kurek Mic] Preferred device [{dname}] needs resampling; using system audio layer")
                    break

    # 2. On Linux, PipeWire/default cleanly handles software resampling
    for name in ("pipewire", "default", "sysdefault"):
        try:
            sd.check_input_settings(device=name, samplerate=SAMPLE_RATE, channels=CHANNELS)
            print(f"[Kurek Mic] Using audio layer [{name}]")
            return name
        except Exception:
            pass

    # 3. Fallback to default input
    default_dev = sd.default.device[0]
    print(f"[Kurek Mic] Using default input device [{default_dev}]")
    return default_dev


class KurekState:
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"



_CONFIRM_AFFIRMATIVE = frozenset({
    "yes", "y", "yep", "yeah", "yup", "sure", "ok", "okay", "confirm", "confirmed",
    "do it", "go ahead", "proceed", "ja", "yes please", "yes do it", "ok do it",
    "okay do it", "sure do it", "yes go ahead", "yes confirm",
})
_CONFIRM_NEGATIVE = frozenset({
    "no", "n", "nope", "nah", "cancel", "stop", "abort", "nein", "dont", "do not",
    "dont do it", "do not do it", "no thanks", "no cancel", "cancel it",
    "no dont", "no stop", "never mind", "nevermind",
})


def is_fresh_start(text: str) -> bool:
    """True when the whole utterance asks to drop the recent chat: "fresh start" or
    "new conversation", optionally after a short lead-in such as "Kurek,"."""
    words = re.sub(r"[^a-z0-9\s]", "", (text or "").lower()).split()
    return len(words) <= 3 and " ".join(words[-2:]) in ("fresh start", "new conversation")


def classify_confirmation_reply(text: str) -> bool | None:
    """True/False when the WHOLE reply is a yes/no word or phrase, else None.

    Matching is on the full normalized utterance, never on a prefix, so
    "notes", "now open x", "ok, what's the time", "surely" or "stop the music"
    are not treated as confirmations.
    """
    norm = re.sub(r"[^a-z0-9\s]", "", (text or "").lower().replace("\u2019", "'").replace("'", ""))
    norm = " ".join(norm.split())
    if norm in _CONFIRM_AFFIRMATIVE:
        return True
    if norm in _CONFIRM_NEGATIVE:
        return False
    return None


PROJECTS_DIR = Path.home() / "dev" / "nodaysidle"


def project_names() -> list[str]:
    """The user's projects: every git repo directly inside PROJECTS_DIR."""
    try:
        return sorted(p.name for p in PROJECTS_DIR.iterdir() if (p / ".git").exists())
    except OSError:
        return []


def history_message(turn: dict) -> dict:
    """A stored turn as a chat message. Assistant turns carry the tools they ran, so a later
    turn can tell whether it really searched instead of guessing from the reply text."""
    content = turn.get("content", "")
    # Only turns that ran tools get the note: a "none" note on every turn taught the model to copy it.
    if turn.get("role") == "assistant" and turn.get("tools"):
        content = f"{content}\n\n(Tool calls in that turn, recorded by the daemon: {'; '.join(turn['tools'])}.)"
    return {"role": turn.get("role", "user"), "content": content}


class KurekEngine:
    def __init__(self):
        self.state = KurekState.IDLE
        self.state_lock = threading.Lock()
        self.audio_buffer = []
        self.is_recording = False
        self.record_stream = None
        self.record_start_time = 0.0
        self.last_sound_time = time.time()
        self.has_speech = False
        self.input_device = _resolve_input_device()

        # Load tools
        print("[Kurek] Discovering tools and actions…")
        try:
            self.actions = discover_actions(BASE_DIR / "actions")
            action_count = len(self.actions.names()) if hasattr(self.actions, "names") else 0
            print(f"[Kurek] Loaded {action_count} actions.")
        except Exception as e:
            print(f"[Kurek] Action loader notice: {e}")
            self.actions = None

        # Build OpenAI tool declarations for DeepSeek
        self.openai_tools = []
        if self.actions and hasattr(self.actions, "get_tool_declarations"):
            for decl in self.actions.get_tool_declarations():
                self.openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": decl["name"],
                        "description": decl.get("description", ""),
                        "parameters": _normalize_json_schema(decl.get("parameters", {})),
                    }
                })
        print(f"[Kurek] Configured {len(self.openai_tools)} tools for {brain_name()}.")

        # Multi-turn conversational memory & persistence
        self.history_file = BASE_DIR / "memory" / "kurek_history.json"
        self.history = self._load_history()
        self._warned_history_cutoff = ""
        print(f"[Kurek Memory] Loaded {len(self.history)} prior conversation turns.")

        # STT engine: cloud (OpenRouter multilingual Whisper, then Deepgram) when a
        # key is set, with local faster-whisper as the runtime fallback when the
        # cloud call fails. Without cloud keys, local faster-whisper only.
        stt_provider = (os.environ.get("KUREK_STT_PROVIDER") or "openrouter").strip().lower()
        or_key = get_openrouter_api_key()
        dg_key = get_deepgram_key()
        or_model = os.environ.get("OPENROUTER_STT_MODEL", "openai/whisper-large-v3-turbo")
        whisper_model = (os.environ.get("KUREK_WHISPER_MODEL") or "base.en").strip()
        # Pin the recogniser when auto-detect guesses wrong (a deep voice in a
        # Slavic country is not automatically Russian). Empty = auto-detect.
        stt_language = (os.environ.get("KUREK_STT_LANGUAGE") or "").strip().lower() or None
        if stt_language == "auto":
            stt_language = None
        if stt_language:
            print(f"[Kurek] STT language pinned to '{stt_language}' (KUREK_STT_LANGUAGE)")

        cloud_stt = None
        if stt_provider == "openrouter" and or_key:
            print(f"[Kurek] Using OpenRouter STT ({or_model}) for multilingual speech recognition")
            cloud_stt = OpenRouterSTT(api_key=or_key, model=or_model, language=stt_language)
        elif dg_key:
            print("[Kurek] Using Deepgram Nova-2 for ultra-fast STT")
            cloud_stt = DeepgramSTT(api_key=dg_key, language=stt_language)
        elif or_key:
            print(f"[Kurek] Using OpenRouter STT ({or_model}) for multilingual speech recognition")
            cloud_stt = OpenRouterSTT(api_key=or_key, model=or_model, language=stt_language)

        if cloud_stt is not None:
            print(f"[Kurek] Local faster-whisper ({whisper_model}) is the fallback if cloud STT fails")
            self.stt = FallbackSTT(
                cloud_stt, lambda: WhisperSTT(model_name=whisper_model, language=stt_language)
            )
        else:
            print(f"[Kurek] Using local faster-whisper ({whisper_model}) STT…")
            try:
                self.stt = WhisperSTT(model_name=whisper_model, language=stt_language)
            except Exception as e:
                print(f"[Kurek] Whisper load error: {e}")
                self.stt = None

        # TTS engine: xAI Grok Cloud TTS (voice: Sol / sal); spd-say fallback
        xai_key = get_xai_key()
        if xai_key:
            print("[Kurek] Using xAI Grok Cloud TTS (voice: Sol/sal)…")
            self.tts = create_tts_player({"tts_engine": "xai", "tts_voice": "sal", "xai_api_key": xai_key})
        else:
            print("[Kurek] No XAI_API_KEY — using local spd-say voice")
            self.tts = create_tts_player({"tts_engine": "system"})

        # Wire sustained resource watcher (300-second CPU/RAM) alert callback
        try:
            from actions.system_monitor import _GLOBAL_WATCHER
            def _on_resource_alert(msg: str):
                print(f"[Kurek] Sustained resource spike detected: {msg}", flush=True)
                if self.state in ("idle", "speaking"):
                    self.set_state("speaking")
                    self.tts.speak(msg)
                    self.set_state("idle")
            _GLOBAL_WATCHER.alert_callback = _on_resource_alert
            print("[Kurek] Sustained resource watcher alert callback attached.")
        except Exception as e:
            print(f"[Kurek] Resource watcher callback notice: {e}")

        # IPC & Hyprland watchers
        self.uds_subscribers: list[socket.socket] = []
        self.uds_lock = threading.Lock()
        self._cancel_requested = False

        # Pre-warm PipeWire mpv sink & start Hyprland event watcher
        try:
            get_mpv_sink().ensure_running()
            print("[Kurek Audio] 🎧 Persistent PipeWire mpv sink initialized.")
        except Exception as e:
            print(f"[Kurek Audio Notice] {e}")

        try:
            get_hyprland_watcher()
            print("[Kurek Hyprland] 🪟 Event-driven Hyprland watcher connected.")
        except Exception as e:
            print(f"[Kurek Hyprland Notice] {e}")

        # Start the background dream-cycle worker (hourly check)
        threading.Thread(target=self._hourly_consolidation_loop, daemon=True).start()
        print("[Kurek Memory] Dream cycle worker initialized.")

        # Wire headless notification callback to confirm system
        def _confirm_show(title: str, detail: str) -> None:
            icon = os.path.expanduser("~/.local/share/icons/kurek.png")
            if not os.path.exists(icon):
                icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop", "kurek.png")
            try:
                subprocess.Popen([
                    "notify-send", "-u", "critical",
                    "-h", "string:x-canonical-private-synchronous:kurek-confirm",
                    "-i", icon, f"Kurek Confirmation: {title}", detail
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

        def _confirm_hide() -> None:
            pass

        def _confirm_log(msg: str) -> None:
            print(f"[Kurek Confirm] {msg}", flush=True)
            if msg.startswith("ERR:"):
                self._notify_failure(msg[4:].strip())

        confirm.bind(_confirm_show, _confirm_hide, _confirm_log)
        print("[Kurek Confirm] Headless confirmation notifications bound.")

        self._history_lock = threading.Lock()
        self._report_startup_problems(xai_key)

    def _report_startup_problems(self, xai_key: str | None) -> None:
        """One notification at startup listing anything that will break usage."""
        problems = []
        if brain_problem := brain_key_problem():
            problems.append(brain_problem)
        if self.stt is None:
            problems.append("No speech recognition (faster-whisper failed to load, no cloud STT key).")
        if not xai_key and not shutil.which("spd-say"):
            problems.append("No voice: set XAI_API_KEY or install speech-dispatcher (spd-say).")
        elif not xai_key:
            problems.append("XAI_API_KEY missing — using the robotic spd-say voice.")
        if not shutil.which("mpv"):
            problems.append("mpv not installed — cloud speech cannot play.")
        if self.input_device is None:
            problems.append("No microphone input device found.")
        for p in problems:
            print(f"[Kurek Startup] ⚠️ {p}", flush=True)
        if problems:
            self._notify_failure("Setup issues: " + " ".join(problems) + " Run `kurek doctor`.")

    def _load_history(self) -> list[dict]:
        try:
            if self.history_file.exists():
                data = json.loads(self.history_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data[-20:]
        except Exception as e:
            print(f"[Kurek Memory] Error reading history: {e}")
        return []

    def _reply_direct(self, reply, callback, mute_speaker, stream_audio):
        """Answer without the LLM: send the reply to the caller and speak it."""
        if callback:
            try:
                audio_b64 = None
                if stream_audio:
                    audio_bytes = self.synthesize_speech_bytes(reply)
                    if audio_bytes:
                        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                ev_payload = {"type": "sentence", "sentence": reply}
                if audio_b64:
                    ev_payload["audio"] = audio_b64
                callback(ev_payload)
                callback({"type": "done", "reply": reply})
            except Exception:
                pass
        if not mute_speaker and not self._cancel_requested:
            self.set_state(KurekState.SPEAKING)
            self.tts.speak(reply)
        return reply

    def _save_history(self):
        try:
            with self._history_lock:
                atomic_write_text(
                    self.history_file,
                    json.dumps(self.history[-30:], indent=2, ensure_ascii=False),
                )
        except Exception as e:
            print(f"[Kurek Memory] Error saving history: {e}")

    def set_state(self, new_state: str):
        with self.state_lock:
            self.state = new_state
            print(f"[Kurek State] → {new_state.upper()}", flush=True)
            self._notify_state(new_state)
            self._broadcast_state_uds(new_state)

    def _broadcast_state_uds(self, state: str):
        payload = (json.dumps({"event": "state", "state": state}) + "\n").encode("utf-8")
        with self.uds_lock:
            dead = []
            for client_sock in self.uds_subscribers:
                try:
                    client_sock.sendall(payload)
                except Exception:
                    dead.append(client_sock)
            for d in dead:
                try:
                    self.uds_subscribers.remove(d)
                    d.close()
                except Exception:
                    pass

    def _notify_state(self, state: str):
        # KUREK_STATE_NOTIFY: "minimal" (default: Listening + Thinking only, the
        # states you can't hear), "all", or "off".
        mode = (os.environ.get("KUREK_STATE_NOTIFY") or "minimal").strip().lower()
        if mode in ("off", "0", "false", "no", "none"):
            return
        if mode != "all" and state not in (KurekState.LISTENING, KurekState.THINKING):
            return
        icon = os.path.expanduser("~/.local/share/icons/kurek.png")
        if not os.path.exists(icon):
            icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop", "kurek.png")
        msg_map = {
            KurekState.LISTENING: "🟢 Listening... (Speak now)",
            KurekState.THINKING:  "🟡 Thinking...",
            KurekState.SPEAKING:  "🔵 Speaking...",
            KurekState.IDLE:      "● Idle",
        }
        msg = msg_map.get(state)
        if msg:
            try:
                subprocess.Popen([
                    "notify-send", "-t", "2000",
                    "-h", "string:x-canonical-private-synchronous:kurek",
                    "-i", icon, "Kurek", msg
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

    def toggle(self):
        """Toggle between idle and listening with instant barge-in."""
        with self.state_lock:
            cur = self.state

        if cur == KurekState.IDLE:
            self._cancel_requested = False
            self.start_listening()
        elif cur == KurekState.LISTENING:
            self.stop_listening_and_process()
        elif cur in (KurekState.SPEAKING, KurekState.THINKING):
            # Instant barge-in / interrupt
            self._cancel_requested = True
            self.tts.stop()
            self.set_state(KurekState.IDLE)

    def start_listening(self):
        self.audio_buffer = []
        self.has_speech = False
        self.speech_chunks = 0
        self.noise_floor = 0.004
        self.peak_rms = 0.0
        self.record_start_time = time.time()
        self.last_sound_time = time.time()
        self.is_recording = True
        self.set_state(KurekState.LISTENING)

        def audio_callback(indata, frames, time_info, status):
            if not self.is_recording:
                return
            audio_chunk = indata[:, 0]
            self.audio_buffer.append(audio_chunk.copy())
            # Remove DC offset to calculate true AC speech RMS
            ac_chunk = audio_chunk - np.mean(audio_chunk)
            rms = float(np.sqrt(np.mean(ac_chunk ** 2)))
            self.peak_rms = max(self.peak_rms, rms)
            warmup = (time.time() - self.record_start_time) < VAD_WARMUP_S
            self.noise_floor, self.speech_chunks, self.has_speech, sound_now = vad_update(
                self.noise_floor, self.speech_chunks, self.has_speech, rms, warmup=warmup
            )
            if sound_now:
                self.last_sound_time = time.time()

        try:
            self.record_stream = sd.InputStream(
                device=self.input_device,
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                callback=audio_callback,
            )
            self.record_stream.start()
        except Exception as e:
            print(f"[Kurek] Mic error: {e}")
            self.set_state(KurekState.IDLE)

    def stop_listening_and_process(self):
        if not self.is_recording:
            return
        self.is_recording = False
        try:
            if self.record_stream:
                self.record_stream.stop()
                self.record_stream.close()
                self.record_stream = None
        except Exception:
            pass

        if not self.audio_buffer:
            print("[Kurek] No audio captured.")
            self.set_state(KurekState.IDLE)
            return

        threading.Thread(target=self._process_recorded_audio, daemon=True).start()

    def _process_recorded_audio(self):
        self.set_state(KurekState.THINKING)
        full_audio = np.concatenate(self.audio_buffer)

        # Transcribe
        transcript = ""
        stt_failure = None
        if self.stt is None:
            stt_failure = "Speech recognition isn't available. Run kurek doctor."
        else:
            try:
                transcript = self.stt.transcribe(full_audio, sample_rate=SAMPLE_RATE)
            except STTError as e:
                print(f"[Kurek] STT failed: {e}", flush=True)
                stt_failure = "My speech recognition failed, so I couldn't understand that."
            except Exception as e:
                import traceback
                print(f"[Kurek] STT error: {e}", flush=True)
                traceback.print_exc()
                stt_failure = "My speech recognition failed, so I couldn't understand that."

        if stt_failure:
            self._notify_failure(stt_failure)
            self.set_state(KurekState.SPEAKING)
            try:
                self.tts.speak(stt_failure)
            except Exception as e:
                print(f"[Kurek] TTS error: {e}")
            self.set_state(KurekState.IDLE)
            return

        print(f"[User Voice] 🎙️ \"{transcript}\"", flush=True)
        if not transcript.strip():
            print("[Kurek] Transcript was empty — speaking audible feedback", flush=True)
            self.set_state(KurekState.SPEAKING)
            try:
                self.tts.speak("I didn't hear anything. Try clicking Kurek and speaking again.")
            except Exception as e:
                print(f"[Kurek] TTS error: {e}")
            self.set_state(KurekState.IDLE)
            return

        self.handle_text_query(transcript)

    def _notify_failure(self, message: str):
        """Surface a short honest failure via notification (and optional speech)."""
        print(f"[Kurek] ❌ {message}", flush=True)
        icon = os.path.expanduser("~/.local/share/icons/kurek.png")
        if not os.path.exists(icon):
            icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop", "kurek.png")
        try:
            subprocess.Popen(
                [
                    "notify-send", "-t", "4000",
                    "-h", "string:x-canonical-private-synchronous:kurek",
                    "-i", icon, "Kurek", message,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def handle_text_query(
        self,
        user_prompt: str,
        callback=None,
        mute_speaker: bool = False,
        remote: bool = False,
        stream_audio: bool = False,
    ):
        begin_query()
        self.tts.begin_stream()
        self.set_state(KurekState.THINKING)
        try:
            return self._handle_text_query_inner(
                user_prompt,
                callback=callback,
                mute_speaker=mute_speaker,
                remote=remote,
                stream_audio=stream_audio,
            )
        except Exception as e:
            print(f"[Kurek] Unhandled query error: {e}", flush=True)
            self._notify_failure("Something went wrong handling that request.")
            try:
                if not self._cancel_requested and not mute_speaker:
                    self.set_state(KurekState.SPEAKING)
                    self.tts.speak("Something went wrong handling that request.")
            except Exception as tts_err:
                print(f"[Kurek] TTS error during failure report: {tts_err}")
            if callback:
                try:
                    callback({"type": "error", "error": str(e)})
                except Exception:
                    pass
        finally:
            # Always return to a resting state — never leave the daemon stuck in THINKING.
            # Sentences are spoken from the TTS queue, so wait for it to drain
            # (barge-in via toggle() cancels immediately).
            try:
                deadline = time.time() + 180.0
                while getattr(self.tts, "is_playing", False) and time.time() < deadline:
                    if self._cancel_requested:
                        break
                    time.sleep(0.05)
            except Exception:
                pass
            if self.state != KurekState.IDLE:
                self.set_state(KurekState.IDLE)
            end_query()

    def _run_tool(self, fn_name: str, fn_args: dict, *, remote: bool = False) -> str:
        """Dispatch a tool call; block exec/shell tools for remote clients by default."""
        if (
            remote
            and not remote_allow_exec()
            and fn_name in REMOTE_BLOCKED_EXEC_TOOLS
        ):
            return (
                f"Tool '{fn_name}' is disabled for remote prompts "
                "(set KUREK_REMOTE_ALLOW_EXEC=1 to allow)."
            )
        return self.actions.run(fn_name, fn_args)

    def _handle_text_query_inner(
        self,
        user_prompt: str,
        callback=None,
        mute_speaker: bool = False,
        remote: bool = False,
        stream_audio: bool = False,
    ):
        # Pending-confirmation shortcut. A short reply that is ONLY a yes/no
        # word or phrase resolves the waiting confirmation; anything else
        # ("notes...", "now...", "ok, what's...", "stop the music") is handled
        # as a normal prompt. Deliberate design (owner decision 2026-10-14):
        # this applies to remote /remote/prompt requests too, so the
        # authenticated Kurek Remote phone app MAY confirm or cancel a
        # confirmation that was raised on the PC.
        if confirm.is_pending():
            verdict = classify_confirmation_reply(user_prompt)
            if verdict is not None:
                confirm.resolve(verdict)
                # resolve() runs the action on a worker thread; failures are
                # reported through the confirm log callback, so don't claim done.
                reply = "Confirmed, doing it now." if verdict else "Action cancelled."
                return self._reply_direct(reply, callback, mute_speaker, stream_audio)

        # Rules, learned memory and daily logs stay; only the rolling chat goes.
        if is_fresh_start(user_prompt):
            with self._history_lock:
                self.history = []
            self._save_history()
            print("[Kurek Memory] Fresh start: conversation history cleared.", flush=True)
            reply = "Fresh start. Rules and memory are kept."
            return self._reply_direct(reply, callback, mute_speaker, stream_audio)

        now_str = datetime.now().strftime("%Y-%m-%d %A, %I:%M %p")

        mem_block = ""

        # Inject Hermes memory from configured / default candidate locations
        hermes_dir = next(
            (d for d in resolve_hermes_memory_dirs() if d.exists() and d.is_dir()),
            None,
        )
        if hermes_dir:
            hermes_snippets = []
            for fname in ("USER.md", "MEMORY.md"):
                fpath = hermes_dir / fname
                if fpath.exists():
                    try:
                        raw = read_capped(fpath)
                        clean = "\n".join(b.strip() for b in raw.split("§") if b.strip())
                        if clean:
                            hermes_snippets.append(f"[{fname.replace('.md', '')}]\n{clean}")
                    except Exception as e:
                        print(f"[Kurek Hermes] Error reading {fname}: {e}")
            if hermes_snippets:
                h_text = "\n\n".join(hermes_snippets)
                mem_block = (mem_block + "\n\n" + h_text).strip()

        # Inject Muse Memory (~/MEMORY.md and ~/ALIGNMENT_SYNTHESIS.md)
        muse_parts = []
        muse_core = Path.home() / "MEMORY.md"
        if muse_core.exists():
            try:
                muse_parts.append(f"[MUSE_CORE_MEMORY]\n{read_capped(muse_core)}")
            except Exception as e:
                print(f"[Kurek Muse] Error reading ~/MEMORY.md: {e}")

        muse_align = Path.home() / "ALIGNMENT_SYNTHESIS.md"
        if muse_align.exists():
            try:
                muse_parts.append(f"[MUSE_ALIGNMENT_SYNTHESIS]\n{read_capped(muse_align)}")
            except Exception as e:
                print(f"[Kurek Muse] Error reading ~/ALIGNMENT_SYNTHESIS.md: {e}")

        if learned := ledger.prompt_block(MEMORY_FILE_MAX_CHARS):
            muse_parts.append(learned)

        if muse_parts:
            mem_block = (mem_block + "\n\n" + "\n\n".join(muse_parts)).strip()

        user_name = (get_user_name() or "the user").strip()
        sys_prompt = (
            f"You are Kurek, an ultra-fast, terse, hyper-competent personal AI assistant. "
            f"Current date and time: {now_str}. "
            f"You are assisting {user_name}. Your voice is powered by xAI Grok (Sol) and your brain is powered directly by {brain_name()}. "
            f"Execute {user_name}'s commands promptly and helpfully. "
            "You are running natively on the user's workstation with local tool access (not a remote sandbox). "
            "You have direct system control access through your built-in tools: "
            "• web_search: live web search and news retrieval. AUTONOMOUS RULE: For any questions about current events, sports results, start times, news, weather, or real-time info, DO NOT GUESS OR SPECULATE. Immediately call web_search with concise keywords. "
            "• CURRENT DATE & 'TODAY' QUERIES: When asked about 'today' or current event results, note today's exact date and search specifically for today. Do NOT confuse today with previous events. "
            "• DEEP SEARCH & REPORT PIPELINE (UNIVERSAL FOR ANY TOPIC): Whenever asked to research, deep search, look into, or investigate ANY topic and save the findings to a file: "
            "  1. AUTONOMOUS END-TO-END EXECUTION: Complete the entire workflow autonomously in the turn sequence without stopping halfway. "
            "  2. Call web_search (running multiple targeted queries if needed) to gather real-time ground truth. "
            "  3. Structure your findings into clean, comprehensive markdown (summary, sections, dates, sources). "
            "  4. Immediately call file_controller with action='create_file', path='<requested_file>.md', and content=... to write it to disk. "
            "  5. In your spoken response, give a punchy 1-2 sentence summary of what you found. State that the file was saved ONLY after file_controller succeeded, specifying the exact path. Never claim a file was written if the write was not executed. "
            "• file_controller: full filesystem management to create, write, append, read, list, and move files anywhere (e.g. ~/Downloads, ~/Desktop, ~/Documents). "
            "  - FILE CREATION PERMISSION: You may create or write files when asked. If asked to make a file, IMMEDIATELY call file_controller with action='create_file' without asking for permission. "
            f"  - FILE DELETION CONFIRMATION: When asked to delete a specific file, call file_controller with action='delete', path=..., confirmed=false. Then ALWAYS ask {user_name}: 'Are you sure you want to delete [filename]? Yes or No?'. NEVER delete a file with confirmed=true unless they have explicitly replied 'Yes'. "
            "• open_app: launch or switch to apps and tools "
            "• browser_control: control desktop browser GUI windows (click, type, scroll, navigate tabs). Do NOT use this tool to look up information — use web_search instead. "
            "• computer_settings: adjust volume, mute, display, and connectivity "
            "• desktop_control: minimize, maximize, hide, or arrange windows "
            "• computer_control: simulate typing, clicks, hotkeys, or capture screenshots "
            "• reminder: schedule alarms and notifications "
            "• manage_memory: store and recall user knowledge, preferences, and notes; pass replaces=<#id> to correct an earlier learned memory "
            "• screen_vision: visual perception of the monitor and active Hyprland windows. Use action='inspect' when asked what is on screen, to diagnose compiler errors, review code, or inspect active windows. Use action='start_watch' to continuously watch until told to stop. "
            "• manage_clipboard: persistent Wayland clipboard manager with snippet pinning and recall. Use action='get_latest' when asked what is in the clipboard. Use action='pin' with an optional title to pin a snippet. Use action='list_pinned' to review saved snippets. Use action='copy' to restore a snippet back into the system clipboard. "
            "• system_monitor: hardware diagnostics and 5-minute sustained CPU/RAM watcher. Use action='status' when asked about system resources, CPU, RAM, or temperature. "
            "• process_sentinel: watch long-running compile, build, test, or training jobs (cargo, npm, python, rustc, make). Use action='watch' with a process name, command, or PID. When the job finishes, Kurek alerts verbally and sends a notification. "
            "• draft_to_clipboard: format dictated text into conventional commits, GitHub PR descriptions, issues, docstrings, or markdown notes and copies immediately to the Wayland system clipboard (wl-copy) for instant pasting with Ctrl+V. "
            "• workstation_radar: parallel health scan across the git repositories under ~/dev. Use action='actionable' to report dirty working trees, untracked files, and unpushed commits ahead of upstream. "
            "• dream_tool: executes the Muse Memory reflection cycle. Synthesizes today's daily log into an atmospheric dream journal (~/dreams/YYYY-MM-DD.md) and writes rule proposals to ~/kurek_proposed_rules.md for the user to review. "
            + _run_tool_prompt_line() +
            "• vault_knowledge: access the nodaysidle-knowledge llm-wiki and Obsidian vault. Use action='list_projects' when asked about what projects exist in the nodaysidle portfolio, what is done, active projects, or releases. Use action='read' with note_name (e.g. 'x-promo-strategy', 'cascade-v3-overview', 'nodaysrammar-overview', 'kureksistant-overview', 'nodaysidle-sonora-overview') to read architecture specs, project PRDs, roadmaps, or social media promotion plans. Use action='search' with a query to look up anything across all notes in the vault. Use action='add_inbox' to capture quick ideas or tasks directly into the vault inbox. "
            "Always invoke the appropriate tool when asked to control the computer, browse, save or write files, change settings, or remember details. "
            "CONVERSATION & OPINIONS: "
            "• When asked for your opinion, thoughts, perspective, or recommendation, provide a direct, candid, and thoughtful opinion with a clear, confident stance. Speak naturally and conversationally. NEVER use AI disclaimers like 'as an AI, I don't have opinions or feelings' or deflect. "
            "• Only offer an opinion when explicitly asked. For standard instructions and computer control commands, stay terse, razor-sharp, and execute immediately without unsolicited commentary, explanation, or fluff. "
            "VOICE & SPEECH RULES: "
            "1. The user only hears your voice over the speaker. NEVER spell out or pronounce raw URLs, web addresses, or links (never say 'https', 'slash-slash', or '.com'). "
            "2. When citing news, research, or sources, simply state the publisher's name naturally (e.g. 'according to Reuters' or 'the BBC reports') without reading links. "
            "3. LENGTH AND LANGUAGE: follow the brevity and language rules in [USER RULES]. Without such a rule, keep spoken answers short and reply in the language of the user's message. Give the answer and stop. "
            "4. NEVER read aloud long lists of filenames, file sizes, or raw system logs over the speaker unless explicitly asked to read every item. Summarize what was found or done in 1 punchy sentence. "
            "5. NO PADDING OR OFFERS: Never justify your decisions (e.g. do not explain why you chose a filename), never analyze the prompt (e.g. do not say 'the name sounds fake'), and never append closing offers like 'Let me know if you want...', 'Say the word if...', or 'Give me X and I will...'. "
            "TASK DISCIPLINE & EXECUTION TRUTH: "
            "1. Task Scope & Brevity: Execute strictly the task given. Do not branch into unrequested work, offer unsolicited follow-ups, or drift into side topics. A one-sentence connection that changes the answer (see CONNECTIONS) is not a side topic. "
            "2. Completed vs. Planned: Never state or imply that an action was completed unless its corresponding tool call executed and succeeded in the current turn. If an action is merely planned, queued, or intended, state plainly that it is planned—never that it is done. "
            "3. File Writes and Sources: Never claim you saved, filed, placed, or recorded sources in a folder or document unless the write operation succeeded in that turn. When reporting a write, state only what was written and the exact destination path in one sentence. Do not justify naming choices or offer renames. If you did not perform the write, do not claim any file was modified. "
            "4. Web Search Integrity: Never invent citations, URLs, snippets, or file paths. If a search yields no relevant results or fails, state the truth in one sentence: say plainly that you found nothing and report the exact query you searched. Do not add commentary or speculation about the query. "
            "5. No Apology Deflections: Apologies are never an acceptable substitute for results or honest reporting. Do not use 'Sorry, Master Alan.' or any variant to close a turn, gloss over an unperformed task, or mask a failure. Report facts, tool outcomes, and errors directly. "
            "6. Clarify Instead of Guessing: If a prompt is ambiguous, blocked, or missing parameters required to act, ask exactly one direct, brief question to resolve the blocker. Do not fabricate missing information or guess. "
            "WHO WINS WHEN SOURCES DISAGREE (highest first): "
            "1. [USER RULES]. "
            "2. The user's current message. If it goes against a lasting fact in [LEARNED MEMORY] (their terminal, name, city, where files go), do not act at all this turn (no tool calls, not even with the remembered value): say what memory holds, ask once whether it changed, and wait for the answer. Save the change with manage_memory (replaces=<#id>) only after they confirm. Something said only for now ('I'm in Koper today', 'use the phone tonight') you simply follow and never save. "
            "3. [LEARNED MEMORY]. "
            "4. [MUSE_CORE_MEMORY] reference facts and Hermes [USER] / [MEMORY]. If one disagrees with learned memory, follow learned memory and tell the user in one sentence so they can fix it. "
            "5. Earlier chat turns and past conversations found by recall: what was said, never proof of what is true now. "
            "RECALL: When the user refers to a past conversation ('two days ago', 'last week', 'what did I say about...') or asks about anything personal you cannot see above, call manage_memory(action='recall') BEFORE saying you don't know. Put several keywords and synonyms in one query (e.g. 'phone apk android crash rotate'). Answer with the date and what was said ('On Tuesday you said...'). What recall finds stays what was said: save it only if the user asks you to remember it. "
            "CONNECTIONS: When you notice a clear link to one of the user's projects or to something said before, and it changes the answer, mention it in one sentence. Never phrase it as an offer. "
            "FILE TASKS: Only when the user asks you to sort, clean up, move, rename or delete files: look first (list the files). A file is connected to a project only when its name or folder clearly names one of the projects listed below. Before moving, renaming or deleting connected files, ask ONE question with the grouped plan (e.g. '3 files look like Kurek Remote builds and 2 like Sonora notes: move them to those projects and keep only the newest APK?'), then act on the answer. Leave any file you are not sure about where it is, and say so. Files with no project link: do what was asked; deletion still needs confirmation."
        )

        vault_anchor = (
            "[NODAYSIDLE PORTFOLIO & KNOWLEDGE VAULT]\n"
            "Vault: nodaysidle-knowledge (Obsidian llm-wiki)\n"
            f"The user's projects (git repos in ~/dev/nodaysidle): {', '.join(project_names()) or 'none found'}.\n"
            "Social Media / X Strategy: wiki/concepts/x-promo-strategy.md, moc-x-promo.md, reel-clip-workflow.md.\n"
            "Whenever asked about portfolio projects, roadmap, what must be done, what is done, or social media strategy, ALWAYS call vault_knowledge(action='read'|'list_projects'|'search') to retrieve ground truth."
        )
        if rules_block := rules_prompt_block():
            sys_prompt += f"\n\n{rules_block}"
        sys_prompt += f"\n\n{vault_anchor}"

        if mem_block:
            sys_prompt += f"\n\n[USER MEMORY & PREFERENCES]\n{mem_block}"

        # Autonomous screen-context detection (Jev → Decision-1 fallback)
        auto_vision_ctx = self._check_auto_screen_context(user_prompt)
        effective_user_prompt = f"{user_prompt}\n\n{auto_vision_ctx}" if auto_vision_ctx else user_prompt

        messages = [
            {"role": "system", "content": sys_prompt},
        ]
        # Last 10 turns for continuity. Turns from before a memory correction made outside the
        # chat may repeat the old fact, and the model believes the chat over memory, so they go.
        cutoff = ledger.outside_correction_ts()
        recent = [t for t in self.history if t.get("ts", "") > cutoff] if cutoff else self.history
        if len(recent) < len(self.history) and cutoff != self._warned_history_cutoff:
            self._warned_history_cutoff = cutoff
            print(f"[Kurek Memory] WARNING: {len(self.history) - len(recent)} chat turns from before "
                  f"the memory correction of {cutoff} are no longer sent to the model.", flush=True)
        for turn in recent[-10:]:
            messages.append(history_message(turn))
        pinned = rules_pinned_block()
        messages.append({"role": "user", "content": f"{effective_user_prompt}\n\n{pinned}" if pinned else effective_user_prompt})

        reply_text = ""
        last_tool_output = ""
        tools_run: list[str] = []
        note_copied = False
        max_tool_turns = 15
        sentence_count = 0
        stream_error: str | None = None

        for turn_idx in range(max_tool_turns):
            if self._cancel_requested:
                break

            tool_detected = False
            streamed_sentences = []

            for ev in stream_deepseek_sentences(
                messages=messages,
                # Final round: forbid tool calls so the model has to answer.
                tools=self.openai_tools or None,
                tool_choice="none" if turn_idx == max_tool_turns - 1 else None,
                model="deepseek-flash",
            ):
                if self._cancel_requested:
                    break

                ev_type = ev.get("type")
                if ev_type == "error":
                    stream_error = ev.get("error") or f"{brain_name()} request failed."
                    break

                if ev_type == "tool_calls":
                    tool_detected = True
                    if callback and stream_audio:
                        # Sentence audio events are emitted by the TTS worker;
                        # flush them so the phone sees them before tool events.
                        self.tts.wait_stream(timeout=30)
                    tool_calls = ev.get("tool_calls", [])
                    asst_msg = {
                        "role": "assistant",
                        "content": ev.get("content") or "",
                        "tool_calls": tool_calls,
                    }
                    if ev.get("reasoning_content"):
                        asst_msg["reasoning_content"] = ev["reasoning_content"]
                    messages.append(asst_msg)

                    last_tool_output = ""
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        fn_name = fn.get("name", "")
                        fn_args_raw = fn.get("arguments", "{}")
                        try:
                            fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                        except Exception:
                            fn_args = {}

                        print(f"[Kurek Tool Dispatch] ⚙️ {fn_name}({fn_args})", flush=True)
                        tools_run.append(f"{fn_name}({json.dumps(fn_args, ensure_ascii=False)[:200]})")
                        try:
                            tool_result = self._run_tool(fn_name, fn_args, remote=remote)
                        except Exception as e:
                            tool_result = f"Error executing {fn_name}: {e}"
                        last_tool_output = str(tool_result)
                        print(f"[Kurek Tool Result] → {tool_result}", flush=True)

                        if callback:
                            try:
                                arg_summary = ", ".join(f"{k}='{v}'" for k, v in list(fn_args.items())[:2]) if isinstance(fn_args, dict) else ""
                                callback({"type": "tool", "name": fn_name, "description": f"⚙️ {fn_name}({arg_summary})", "result": str(tool_result)[:100]})
                            except Exception:
                                pass

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", f"call_{fn_name}_{turn_idx}"),
                            "content": str(tool_result),
                        })
                    # Break out to next turn to process tool result
                    break

                elif ev_type == "sentence":
                    raw_sentence = ev.get("sentence", "")
                    # The model copied the history's tool note: speak nothing from there on.
                    if "Tool calls in that turn" in raw_sentence:
                        note_copied = True
                    if note_copied:
                        continue
                    clean_sentence = self.sanitize_text_for_speech(raw_sentence)
                    if clean_sentence and not self._cancel_requested:
                        self._queue_sentence(
                            raw_sentence, clean_sentence, sentence_count,
                            mute_speaker=mute_speaker, callback=callback, stream_audio=stream_audio,
                        )
                        sentence_count += 1
                        streamed_sentences.append(raw_sentence)

                elif ev_type == "done":
                    reply_text = ev.get("full_content", "") or " ".join(streamed_sentences)
                    break

            if stream_error:
                break
            if tool_detected:
                continue
            else:
                break

        if stream_error or (not reply_text and not last_tool_output and sentence_count == 0):
            if stream_error:
                failure_msg = f"I couldn't reach {brain_name()} right now."
                detail = stream_error
            else:
                failure_msg = f"I didn't get a response from {brain_name()}."
                detail = "empty stream"
            print(f"[Kurek] Stream failure ({detail})", flush=True)
            self._notify_failure(failure_msg)
            if not self._cancel_requested:
                self.set_state(KurekState.SPEAKING)
                try:
                    self.tts.speak(failure_msg)
                except Exception as e:
                    print(f"[Kurek] TTS error: {e}")
            return

        if not reply_text and sentence_count == 0 and last_tool_output:
            # DeepSeek ran tools but did not emit spoken response tokens. Request a 1-sentence synthesis.
            try:
                summary_msgs = list(messages)
                summary_msgs.append({
                    "role": "user",
                    "content": "In spoken sentences, following the brevity rule, directly tell me what was done or found.",
                })
                for ev in stream_deepseek_sentences(
                    messages=summary_msgs, tools=self.openai_tools or None, tool_choice="none", model="deepseek-flash"
                ):
                    if ev.get("type") == "sentence":
                        raw_sentence = ev.get("sentence", "")
                        clean_sentence = self.sanitize_text_for_speech(raw_sentence)
                        if clean_sentence and not self._cancel_requested:
                            self._queue_sentence(
                                raw_sentence, clean_sentence, sentence_count,
                                mute_speaker=mute_speaker, callback=callback, stream_audio=stream_audio,
                            )
                            sentence_count += 1
                            streamed_sentences.append(raw_sentence)
                    elif ev.get("type") == "done":
                        reply_text = ev.get("full_content", "") or " ".join(streamed_sentences)
                        break
            except Exception as e:
                print(f"[Kurek] Summary synthesis notice: {e}")

        if not reply_text and last_tool_output:
            if "error" in last_tool_output.lower():
                reply_text = f"Notice: {last_tool_output.strip()[:140]}"
            elif len(last_tool_output) <= 120 and "\n" not in last_tool_output:
                reply_text = last_tool_output.strip()
            else:
                reply_text = "Done."

        # Strip DeepSeek safety tags, thinking wrappers, and DSML markup if present
        reply_text = re.sub(r"<ds_safety>.*?</ds_safety>", "", reply_text, flags=re.DOTALL)
        reply_text = re.sub(r"<think>.*?</think>", "", reply_text, flags=re.DOTALL)
        reply_text = re.sub(r"<[｜|]{2}DSML[｜|]{2}\s*calls?>.*?</[｜|]{2}DSML[｜|]{2}\s*calls?>", "", reply_text, flags=re.DOTALL)
        reply_text = re.sub(r"<[｜|]{2}DSML[｜|]{2}.*?>", "", reply_text)
        reply_text = re.sub(r"</[｜|]{2}DSML[｜|]{2}.*?>", "", reply_text)
        reply_text = re.sub(r"<[｜|].*?[｜|]>", "", reply_text)
        reply_text = re.sub(r"</[｜|].*?[｜|]>", "", reply_text)
        reply_text = re.sub(r"\(Tool calls in that turn, recorded by the daemon:.*", "", reply_text, flags=re.DOTALL)
        reply_text = reply_text.strip()
        if not reply_text:
            # Successful tool path with no spoken content — not a transport failure.
            reply_text = "All set."

        print(f"[Kurek Reply] 💬 \"{reply_text}\"", flush=True)

        # Update multi-turn history & persist
        ts = datetime.now().isoformat(timespec="seconds")
        self.history.append({"role": "user", "content": user_prompt, "ts": ts})
        self.history.append({"role": "assistant", "content": reply_text, "tools": tools_run, "ts": ts})
        self._save_history()

        # Muse Daily Log & Asynchronous Jev Memory Triage
        self._append_daily_memory(user_prompt, reply_text)
        threading.Thread(target=self._consolidate_with_jev, args=(user_prompt,), daemon=True).start()

        # Fallback speak if sentences were not streamed directly
        if sentence_count == 0 and not self._cancel_requested and not mute_speaker:
            clean_speech = self.sanitize_text_for_speech(reply_text)
            if not clean_speech:
                clean_speech = "All set."
            self.set_state(KurekState.SPEAKING)
            try:
                self.tts.speak(clean_speech)
            except Exception as e:
                print(f"[Kurek] TTS error: {e}")

        if callback:
            if stream_audio:
                # Let queued sentence audio reach the phone before "done".
                self.tts.wait_stream(timeout=60)
            try:
                done_payload = {"type": "done", "reply": reply_text}
                if stream_audio and sentence_count == 0 and reply_text:
                    clean_reply = self.sanitize_text_for_speech(reply_text)
                    audio_bytes = self.synthesize_speech_bytes(clean_reply)
                    if audio_bytes:
                        done_payload["audio"] = base64.b64encode(audio_bytes).decode("utf-8")
                callback(done_payload)
            except Exception:
                pass

        return reply_text

    def synthesize_speech_bytes(self, text: str) -> bytes | None:
        """Audio bytes for text via the configured TTS engine (no playback)."""
        if not text or not text.strip():
            return None
        return self.tts.synthesize(text)

    def _queue_sentence(
        self,
        raw_sentence: str,
        clean_sentence: str,
        index: int,
        *,
        mute_speaker: bool,
        callback,
        stream_audio: bool,
    ) -> None:
        """Hand one streamed sentence to the TTS queue and/or the SSE callback.

        Speech is synthesized once by the TTS worker: the same bytes play on the
        PC speaker (unless muted) and go to the phone (when stream_audio).
        """
        if index == 0 and not mute_speaker:
            self.set_state(KurekState.SPEAKING)

        def _send(payload: dict) -> None:
            try:
                callback(payload)
            except Exception as e:
                print(f"[Kurek Remote] SSE callback error: {e}", flush=True)

        on_audio = None
        if callback and stream_audio:
            def on_audio(audio: bytes | None) -> None:
                payload = {"type": "sentence", "sentence": raw_sentence}
                if audio:
                    payload["audio"] = base64.b64encode(audio).decode("utf-8")
                _send(payload)
        elif callback:
            _send({"type": "sentence", "sentence": raw_sentence})

        if not mute_speaker or on_audio is not None:
            self.tts.enqueue(clean_sentence, play_local=not mute_speaker, on_audio=on_audio)

    @staticmethod
    def sanitize_text_for_speech(text: str) -> str:
        """Strips URLs, converts markdown links to plain names, and cleans formatting for speech."""
        if not text:
            return ""
        # 1. Convert markdown links [Label](url) -> Label
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        # 2. Strip any raw URLs (http://, https://, www., domain.tld/...)
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'\bwww\.[a-zA-Z0-9.-]+\S*', '', text)
        text = re.sub(r'\b[a-zA-Z0-9.-]+\.(?:com|org|net|gov|edu|io|co|ai|si|de|uk)/\S*', '', text)
        # 3. Strip code blocks and inline code
        text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
        text = re.sub(r'`[^`]*`', '', text)
        # 4. Strip markdown syntax symbols
        text = re.sub(r'[*#_`~>|]', '', text)
        # 5. Clean list bullets and numbering
        text = re.sub(r'^\s*[-*•+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+[\.\)]\s+', '', text, flags=re.MULTILINE)
        # 6. Replace newlines with a natural pause
        text = re.sub(r'[\r\n]+', '. ', text)
        text = re.sub(r'\s{2,}', ' ', text)
        # 7. Remove leftover brackets and parenthesis
        text = re.sub(r'[\[\]\(\)\{\}]', '', text)
        return text.strip()

    def _append_daily_memory(self, user_prompt: str, reply_text: str):
        """Appends each live dialogue turn to ~/memory/YYYY-MM-DD.md for the Muse daily log."""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            daily_dir = Path.home() / "memory"
            daily_dir.mkdir(parents=True, exist_ok=True)
            daily_file = daily_dir / f"{today}.md"
            ts = datetime.now().strftime("%H:%M:%S")
            entry = f"\n### [{ts}] turn\n**User:** {user_prompt.strip()}\n**Kurek:** {reply_text.strip()}\n"
            with open(daily_file, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            print(f"[Muse Memory] Error appending to daily memory: {e}")

    def _consolidate_with_jev(self, user_prompt: str):
        """Snap-judgment triage of single conversation turn using TypeSafe Jev."""
        try:
            import os
            from memory.config_manager import load_api_keys
            load_api_keys()
            if not os.environ.get("TYPESAFE_API_KEY"):
                return
            from typesafe_sdk import TypeSafeClient, Noul, Choice, Score
            client = TypeSafeClient()
            resp = client.system_one(
                state={"prompt": user_prompt},
                questions={
                    "is_durable": Noul(
                        instructions="Does `prompt` state a durable personal preference, identity detail, system rule, or boundary that should be remembered permanently?"
                    ),
                    "kind": Choice(
                        instructions="What category is this information?",
                        criteria={
                            "preference": "User likes, dislikes, habits, or technical preferences",
                            "boundary": "Strict operational rule, forbidden behavior, or hard constraint",
                            "fact": "Static technical, hardware, location, or project detail",
                            "transient": "Temporary note, short-term task, or conversational passing detail",
                            "other": "None of the above"
                        }
                    ),
                    "salience": Score(
                        instructions="How critical is this memory for future interactions?",
                        criteria=[
                            "Disposable note or temporary context",
                            "Useful contextual detail",
                            "Permanent core preference, strict boundary, or system constraint"
                        ]
                    )
                }
            )
            is_durable = resp.answers["is_durable"].noul > 0.70
            kind = resp.answers["kind"].choice
            salience = resp.answers["salience"].score
            conf = resp.answers["salience"].confidence

            if is_durable and salience >= 1.5 and kind in ("preference", "boundary", "fact"):
                today = datetime.now().strftime("%Y-%m-%d")
                fact = " ".join(user_prompt.split())
                source = f"daily/{today}.md (jev:{salience:.1f}, conf:{conf:.2f})"
                memory_id = ledger.append(kind, fact, source, heard=fact)
                print(f"[Muse Jev] 🧠 Learned durable {kind} as memory #{memory_id}: {fact[:60]}...", flush=True)

                if kind == "boundary" and propose(
                    f"jev (boundary, salience {salience:.1f})", fact, heard=fact, source=f"daily/{today}.md"
                ):
                    print("[Muse Jev] 🛡️ Proposed boundary in kurek_proposed_rules.md", flush=True)
        except Exception as e:
            print(f"[Muse Jev Consolidation Notice] {e}", flush=True)

    def _check_auto_screen_context(self, user_prompt: str) -> str | None:
        """Detect whether the prompt needs on-screen / terminal visual context.

        One decision call through ``core.decision_client`` (Jev → Decision-1 on
        OpenRouter), each with its own timeout. With no key, or no valid answer,
        the heuristic path returns None and skips screen capture. The prompt text is sent to whichever provider answers.
        """
        try:
            if not auto_screen_enabled():
                return None

            load_api_keys()

            prob, source_engine = query_needs_screen(user_prompt)

            if prob is not None and prob > SCREEN_CONTEXT_THRESHOLD:
                print(
                    f"[Kurek Vision Engine] Auto screen context triggered by "
                    f"{source_engine} (P={prob:.2f}). Capturing active window...",
                    flush=True,
                )
                from actions.screen_vision import (
                    capture_screen_jpeg,
                    get_hyprland_context,
                    query_vision,
                )
                hypr = get_hyprland_context()
                win_title = hypr.get("title", "Unknown")
                win_class = hypr.get("class", "Unknown")
                jpeg_bytes, _ = capture_screen_jpeg()
                vision_summary = query_vision(
                    jpeg_bytes,
                    f"Identify any relevant code, error, or UI context in this "
                    f"window relating to: {user_prompt}",
                )
                return (
                    f"[AUTOMATIC LIVE SCREEN CONTEXT]\n"
                    f"Active Window: '{win_title}' (App: {win_class})\n"
                    f"Vision Analysis:\n{vision_summary}"
                )
        except Exception as e:
            msg = str(e)
            if len(msg) > 120:
                msg = msg[:117] + "..."
            print(
                f"[Kurek Vision Engine Notice] {type(e).__name__}: {msg}",
                flush=True,
            )
        return None

    def _hourly_consolidation_loop(self):
        """Dream reflection on yesterday's log, once per day after 05:00.

        execute_dream_cycle skips when yesterday's dream already exists and
        writes nothing on an LLM failure, so checking hourly retries failures
        and catches up when the PC was off at 05:00.
        """
        time.sleep(300)
        while True:
            try:
                if datetime.now().hour >= 5:
                    from core.dream_cycle import execute_dream_cycle
                    d_res = execute_dream_cycle(force=False)
                    status = d_res.get("status")
                    if status == "success":
                        print(f"[Muse Dream] 🌙 Dream journal created: {d_res.get('dream_file')}", flush=True)
                    elif status == "error":
                        print(f"[Muse Dream] Dream cycle failed, will retry next hour: {d_res.get('message')}", flush=True)
            except Exception as e:
                print(f"[Muse Dream] Dream cycle notice: {e}", flush=True)
            time.sleep(3600)



class KurekHTTPHandler(BaseHTTPRequestHandler):
    engine: KurekEngine = None
    # Tests may force non-loopback semantics while connecting via 127.0.0.1.
    force_remote_client: bool | None = None

    def log_message(self, format, *args):
        # Suppress routine polling logs
        pass

    def _peer_is_loopback(self) -> bool:
        if self.force_remote_client is True:
            return False
        if self.force_remote_client is False:
            return True
        host = self.client_address[0] if self.client_address else ""
        return is_loopback_host(host)

    def _send_json(self, code: int, payload: dict | list | str | bytes) -> None:
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = payload
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _loopback_csrf_blocked(self) -> bool:
        """Block browser CSRF to loopback: Origin present, or non-JSON Content-Type."""
        if self.headers.get("Origin"):
            return True
        if self.command == "POST":
            ctype_raw = self.headers.get("Content-Type")
            if ctype_raw is not None:
                ctype = ctype_raw.split(";")[0].strip().lower()
                if ctype != "application/json":
                    return True
        return False

    def _authorize(self, *, allow_unauth_remote_status: bool = False) -> bool:
        """
        Return True if the request may proceed.

        Loopback: /status, /toggle, /prompt stay token-free (Hyprland/Waybar/macOS),
        but CSRF probes are rejected. Remote: GET /status may stay unauthenticated
        for APK online checks; every remote POST requires Bearer auth.
        """
        if self._peer_is_loopback():
            if self._loopback_csrf_blocked():
                self._send_json(403, {"error": "Forbidden"})
                return False
            return True

        if allow_unauth_remote_status and self.command == "GET" and self.path == "/status":
            return True

        configured = get_remote_token()
        if not remote_token_is_strong(configured):
            self._send_json(401, {"error": "Unauthorized: remote token not configured"})
            return False

        provided = parse_bearer_token(self.headers.get("Authorization"))
        if provided is None or not tokens_match(provided, configured):
            self._send_json(401, {"error": "Unauthorized: invalid or missing token"})
            return False
        return True

    def _read_json_body(self) -> dict | None:
        length_hdr = self.headers.get("Content-Length")
        if length_hdr is None or length_hdr == "":
            length = 0
        else:
            try:
                length = int(length_hdr)
            except ValueError:
                self._send_json(400, {"error": "Invalid Content-Length"})
                return None
        if length < 0:
            self._send_json(400, {"error": "Invalid Content-Length"})
            return None
        if length > HTTP_MAX_BODY_BYTES:
            self._send_json(413, {"error": "Payload too large"})
            return None
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "Invalid JSON"})
            return None
        if data is None:
            return {}
        if not isinstance(data, dict):
            self._send_json(400, {"error": "JSON object required"})
            return None
        return data

    def do_GET(self):
        if self.path != "/status":
            self.send_response(404)
            self.end_headers()
            return
        if not self._authorize(allow_unauth_remote_status=True):
            return
        self._send_json(200, {"state": self.engine.state})

    def do_OPTIONS(self):
        # No CORS: the Android app does not need browser cross-origin access.
        self.send_response(405)
        self.end_headers()

    def do_POST(self):
        if self.path == "/toggle":
            if not self._authorize():
                return
            # Drain optional empty/small body without requiring JSON fields.
            if self._read_json_body() is None:
                return
            self.engine.toggle()
            self._send_json(200, {"ok": True, "state": self.engine.state})
            return

        if self.path == "/prompt":
            if not self._authorize():
                return
            data = self._read_json_body()
            if data is None:
                return
            prompt_text = data.get("prompt", "")
            is_remote = not self._peer_is_loopback()
            threading.Thread(
                target=self.engine.handle_text_query,
                kwargs={
                    "user_prompt": prompt_text,
                    "remote": is_remote,
                },
                daemon=True,
            ).start()
            self._send_json(200, {"status": "processing"})
            return

        if self.path == "/remote/prompt":
            # Always require Bearer auth on this endpoint (APK path), even on loopback.
            if self._peer_is_loopback() and self._loopback_csrf_blocked():
                self._send_json(403, {"error": "Forbidden"})
                return
            configured = get_remote_token()
            if not remote_token_is_strong(configured):
                self._send_json(401, {"error": "Unauthorized: remote token not configured"})
                return
            provided = parse_bearer_token(self.headers.get("Authorization"))
            if provided is None or not tokens_match(provided, configured):
                self._send_json(401, {"error": "Unauthorized: invalid or missing token"})
                return

            data = self._read_json_body()
            if data is None:
                return
            prompt_text = data.get("prompt", "")
            stream_audio = bool(data.get("stream_audio", False))
            speak_local = (
                data.get("speak_local") is True
                or os.environ.get("KUREK_REMOTE_LOCAL_TTS") == "1"
            )
            mute_speaker = not speak_local
            if not prompt_text:
                self._send_json(400, {"error": "prompt is required"})
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            # Close after "done": keep-alive would leave the client waiting on an
            # unterminated body (no Content-Length) after the stream ends.
            self.send_header("Connection", "close")
            self.end_headers()

            stream_lock = threading.Lock()

            def _sse_callback(event: dict):
                try:
                    with stream_lock:
                        payload = f"data: {json.dumps(event)}\n\n".encode("utf-8")
                        self.wfile.write(payload)
                        self.wfile.flush()
                except Exception:
                    pass

            is_remote = not self._peer_is_loopback()
            # Execute query synchronously inside this HTTP thread so the SSE connection stays open
            self.engine.handle_text_query(
                prompt_text,
                callback=_sse_callback,
                mute_speaker=mute_speaker,
                remote=is_remote,
                stream_audio=stream_audio,
            )
            return

        self.send_response(404)
        self.end_headers()


def _private_runtime_dir() -> Path:
    """Per-user private runtime dir (0700) when XDG_RUNTIME_DIR is unset."""
    base = Path.home() / ".cache" / "kurek" / "run"
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def _get_uds_socket_path() -> Path:
    """Resolve the UDS path. Never falls back to /tmp (world-writable)."""
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        base = Path(xdg)
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError(
                f"XDG_RUNTIME_DIR={xdg!r} is not usable: {e}"
            ) from e
        return base / "kurek.sock"

    base = _private_runtime_dir()
    print(
        "[Kurek UDS] XDG_RUNTIME_DIR unset — using private "
        f"{base}/kurek.sock (mode 0700 dir)",
        flush=True,
    )
    return base / "kurek.sock"


def _peer_uid(conn: socket.socket) -> int | None:
    """Return the peer UID via SO_PEERCRED (Linux), or None on failure."""
    if sys.platform != "linux":
        # macOS / other: no portable SO_PEERCRED; treat as same-uid.
        return os.getuid()
    so_peercred = getattr(socket, "SO_PEERCRED", 17)
    try:
        creds = conn.getsockopt(socket.SOL_SOCKET, so_peercred, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", creds)
        return int(uid)
    except OSError:
        return None


def _uds_peer_allowed(conn: socket.socket) -> bool:
    """Accept only connections from the same UID as the daemon."""
    peer = _peer_uid(conn)
    if peer is None:
        return False
    return peer == os.getuid()


def run_uds_server(engine: KurekEngine):
    sock_path = _get_uds_socket_path()
    if sock_path.exists():
        try:
            test_s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            test_s.settimeout(0.15)
            test_s.connect(str(sock_path))
            test_s.close()
            print(f"[Kurek UDS] Socket {sock_path} is currently active.")
            return
        except Exception:
            sock_path.unlink(missing_ok=True)

    # Restrict the socket inode to the owning user only.
    old_umask = os.umask(0o177)
    try:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
    finally:
        os.umask(old_umask)
    try:
        os.chmod(sock_path, 0o600)
    except OSError as e:
        print(f"[Kurek UDS] Warning: could not chmod 0600 {sock_path}: {e}", flush=True)
    server.listen(16)
    print(f"[Kurek UDS] 🚀 Listening at Unix Domain Socket: {sock_path} (0600)", flush=True)

    def _client_handler(conn: socket.socket):
        is_subscriber = False
        try:
            if not _uds_peer_allowed(conn):
                try:
                    conn.sendall(b'{"error":"unauthorized"}\n')
                except Exception:
                    pass
                return

            conn.settimeout(None)
            f = conn.makefile("r", encoding="utf-8", errors="replace")
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    data = {"action": line}

                action = data.get("action", "")

                if action == "toggle":
                    engine.toggle()
                    conn.sendall(json.dumps({"ok": True, "state": engine.state}).encode("utf-8") + b"\n")
                    break

                elif action == "prompt":
                    prompt_text = data.get("prompt", "")
                    if prompt_text:
                        threading.Thread(target=engine.handle_text_query, args=(prompt_text,), daemon=True).start()
                    conn.sendall(b'{"status":"processing"}\n')
                    break

                elif action == "status":
                    conn.sendall(json.dumps({"state": engine.state}).encode("utf-8") + b"\n")
                    break

                elif action == "stop":
                    engine.tts.stop()
                    engine.set_state(KurekState.IDLE)
                    conn.sendall(b'{"ok":true}\n')
                    break

                elif action == "subscribe":
                    with engine.uds_lock:
                        engine.uds_subscribers.append(conn)
                    conn.sendall(json.dumps({"event": "state", "state": engine.state}).encode("utf-8") + b"\n")
                    is_subscriber = True
                    # Keep the socket open for state broadcasts until the peer disconnects.
                    # Do not return immediately — the outer finally would close the socket and
                    # leave a dead entry that breaks Waybar/AGS state streaming.
                    try:
                        for _ in f:
                            pass
                    except Exception:
                        pass
                    return

                else:
                    conn.sendall(b'{"error":"unknown action"}\n')
                    break
        except Exception:
            pass
        finally:
            if is_subscriber:
                with engine.uds_lock:
                    if conn in engine.uds_subscribers:
                        try:
                            engine.uds_subscribers.remove(conn)
                        except ValueError:
                            pass
            try:
                conn.close()
            except Exception:
                pass

    while True:
        try:
            conn, _ = server.accept()
            # Peer UID is re-checked inside the handler; reject early here too.
            if not _uds_peer_allowed(conn):
                try:
                    conn.sendall(b'{"error":"unauthorized"}\n')
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                continue
            threading.Thread(target=_client_handler, args=(conn,), daemon=True).start()
        except Exception:
            time.sleep(0.5)


def _start_http_server(host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), KurekHTTPHandler)
    threading.Thread(
        target=server.serve_forever,
        name=f"kurek-http-{host}:{port}",
        daemon=True,
    ).start()
    print(f"[Kurek Server] 🚀 Listening at http://{host}:{port}", flush=True)
    return server


def _tailscale_rebind_loop(port: int, bound_hosts: set[str], stop_event: threading.Event) -> None:
    """Retry Tailscale bind until an IPv4 appears; never crash the daemon."""
    while not stop_event.wait(5.0):
        try:
            require_strong_remote_token_for_bind()
        except RuntimeError as exc:
            print(f"[Kurek Server] Tailscale re-bind paused: {exc}", flush=True)
            continue
        ts_ip = detect_tailscale_ipv4()
        if not ts_ip or ts_ip in bound_hosts:
            continue
        try:
            _start_http_server(ts_ip, port)
            bound_hosts.add(ts_ip)
            print(
                f"[Kurek Server] Tailscale interface ready — bound remote HTTP on {ts_ip}:{port}",
                flush=True,
            )
        except OSError as exc:
            print(
                f"[Kurek Server] Could not bind Tailscale address {ts_ip}:{port}: {exc}",
                flush=True,
            )


def _live_authorized(authorization: str | None) -> bool:
    # Live voice spends xAI minutes, so it needs the token even on loopback.
    token = get_remote_token()
    provided = parse_bearer_token(authorization)
    return remote_token_is_strong(token) and provided is not None and tokens_match(provided, token)


def run_server(engine: KurekEngine, port: int = 8790):
    KurekHTTPHandler.engine = engine
    # Launch UDS listener in background
    threading.Thread(target=run_uds_server, args=(engine,), daemon=True).start()

    try:
        hosts, watch_tailscale = resolve_http_bind_hosts()
    except RuntimeError as exc:
        print(f"[Kurek Server] {exc}", flush=True)
        print(
            "[Kurek Server] Falling back to loopback-only bind (127.0.0.1).",
            flush=True,
        )
        hosts, watch_tailscale = ["127.0.0.1"], False

    bound: set[str] = set()
    primary: ThreadingHTTPServer | None = None
    for host in hosts:
        try:
            server = ThreadingHTTPServer((host, port), KurekHTTPHandler)
            bound.add(host)
            print(f"[Kurek Server] 🚀 Listening at http://{host}:{port}", flush=True)
            if primary is None:
                primary = server
            else:
                threading.Thread(
                    target=server.serve_forever,
                    name=f"kurek-http-{host}:{port}",
                    daemon=True,
                ).start()
        except OSError as exc:
            print(
                f"[Kurek Server] Failed to bind http://{host}:{port}: {exc}",
                flush=True,
            )

    if primary is None:
        # Last-resort loopback so local trigger/status still work.
        primary = ThreadingHTTPServer(("127.0.0.1", port), KurekHTTPHandler)
        bound.add("127.0.0.1")
        print(f"[Kurek Server] 🚀 Listening at http://127.0.0.1:{port}", flush=True)

    if live_voice_enabled():
        start_live_servers(
            sorted(bound),
            _live_authorized,
            engine.openai_tools,
            lambda name, args: engine._run_tool(name, args, remote=True),
        )

    stop_event = threading.Event()
    if watch_tailscale:
        threading.Thread(
            target=_tailscale_rebind_loop,
            args=(port, bound, stop_event),
            name="kurek-tailscale-rebind",
            daemon=True,
        ).start()

    try:
        primary.serve_forever()
    finally:
        stop_event.set()


if __name__ == "__main__":
    print("=" * 60)
    print("⚡ KUREK DAEMON STARTING (Arch Linux / Omarchy Native Mode)")
    print("=" * 60)

    engine = KurekEngine()

    # Background auto-silence watchdog
    stop_messages = {
        "silence": f"Silence detected after speech (> {SILENCE_DURATION}s) — auto-submitting…",
        "no_speech": f"No speech detected within {INITIAL_SILENCE_TIMEOUT}s "
        f"(peak RMS {{peak:.4f}} vs gate ~{{gate:.4f}}) — auto-stopping listening…",
        "max_duration": f"Max recording duration reached ({MAX_RECORD_SECONDS}s) — auto-submitting…",
    }

    def silence_watchdog():
        while True:
            time.sleep(0.1)
            if engine.is_recording:
                reason = silence_stop_reason(
                    time.time(), engine.has_speech, engine.last_sound_time, engine.record_start_time
                )
                if reason:
                    if reason == "silence":
                        window = effective_silence_window(
                            engine.last_sound_time, engine.record_start_time
                        )
                        print(
                            f"[Kurek] Silence detected after speech "
                            f"(> {window:.1f}s, adaptive) — auto-submitting…",
                            flush=True,
                        )
                    elif reason == "no_speech":
                        gate = max(0.006, engine.noise_floor * 2.0)
                        print(
                            stop_messages[reason].format(
                                peak=getattr(engine, "peak_rms", 0.0), gate=gate
                            ),
                            flush=True,
                        )
                    else:
                        print(f"[Kurek] {stop_messages[reason]}", flush=True)
                    engine.stop_listening_and_process()

    threading.Thread(target=silence_watchdog, daemon=True).start()

    run_server(engine)
