"""
Text-to-Speech for Kurek.

XAITTSEngine      – xAI Grok cloud TTS (voice: Sol / "sal"), played through the
                    persistent mpv PipeWire sink.
SystemSpeechEngine – local fallback via `spd-say` (speech-dispatcher), or `say`
                    on macOS.

TTSPlayer wraps an engine. Besides blocking speak(), it runs a streaming
sentence queue: the daemon enqueues sentences as DeepSeek produces them and a
single worker thread synthesizes them in order, appending each clip to mpv.
The LLM stream therefore never waits on a TTS round trip, and clip N+1 is
synthesized while clip N plays.
"""
from __future__ import annotations

import os
import queue as _queue
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Callable, Optional

import requests

import sounddevice as sd

from core.timing import mark as timing_mark


# ---------------------------------------------------------------------------
# Audio playback helpers
# ---------------------------------------------------------------------------

def _play_audio_bytes(audio_bytes: bytes, append: bool = False) -> None:
    """Play MP3/WAV/OGG bytes via the PipeWire mpv sink, else a one-shot player."""
    try:
        from core.mpv_sink import get_mpv_sink
        if get_mpv_sink().play_bytes(audio_bytes, append=append):
            return
    except Exception as e:
        print(f"[AudioPlay] PipeWire mpv sink notice: {e}")

    player = next(
        (p for p in ("mpv", "pw-play", "paplay", "aplay") if shutil.which(p)),
        None,
    )
    if player is None:
        print("[AudioPlay] No audio player found (install mpv).")
        return

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_bytes)
            temp_path = f.name
        cmd = [player]
        if player == "mpv":
            cmd.extend(["--no-video", "--really-quiet"])
        cmd.append(temp_path)
        subprocess.run(cmd, check=False, timeout=120)
    except Exception as e:
        print(f"[AudioPlay] {player} playback notice: {e}")
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

class SystemSpeechEngine:
    """Local speech fallback: `spd-say` on Linux, `say` on macOS."""

    def __init__(self, voice: str = ""):
        self.voice = voice

    @staticmethod
    def available() -> bool:
        return bool(shutil.which("spd-say") or (sys.platform == "darwin" and shutil.which("say")))

    def speak(self, text: str, append: bool = False) -> None:
        if sys.platform == "darwin" and shutil.which("say"):
            cmd = ["say"] + (["-v", self.voice] if self.voice else []) + [text]
        elif shutil.which("spd-say"):
            # -w: wait until spoken, so the daemon's SPEAKING state is truthful.
            cmd = ["spd-say", "-w", text]
        else:
            print(f"[Speech Synth] No spd-say available; cannot speak: {text}")
            return
        try:
            subprocess.run(cmd, check=False, timeout=120)
        except Exception as e:
            print(f"[Speech Synth] Error: {e}")


# Backwards-compatible name used by older callers.
MacSayEngine = SystemSpeechEngine


_XAI_VOICE_MAP = {
    "sol": "sal",
}


class XAITTSEngine:
    """xAI (Grok) Cloud Text-to-Speech Engine."""

    def __init__(self, api_key: str | None = None, voice_id: str = "sal", language: str = "en"):
        from memory.config_manager import get_xai_key
        self.api_key = api_key or get_xai_key() or os.environ.get("XAI_API_KEY", "")
        raw_voice = (voice_id or "sal").strip().lower()
        self.voice_id = _XAI_VOICE_MAP.get(raw_voice, raw_voice)
        self.language = language or "en"
        self._fallback = SystemSpeechEngine()
        self._http = requests.Session()

    def synthesize(self, text: str) -> bytes | None:
        """Return MP3 bytes for text, or None on any failure."""
        if not text.strip() or not self.api_key:
            return None
        try:
            resp = self._http.post(
                "https://api.x.ai/v1/tts",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"text": text.strip(), "voice_id": self.voice_id, "language": self.language},
                timeout=12,
            )
            if resp.status_code == 200 and resp.content:
                return resp.content
            print(f"[xAI TTS] HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"[xAI TTS] Error: {e}")
        return None

    def speak(self, text: str, append: bool = False) -> None:
        if not text.strip():
            return
        audio = self.synthesize(text)
        if audio:
            _play_audio_bytes(audio, append=append)
        else:
            self._fallback.speak(text)

    def stop(self) -> None:
        try:
            from core.mpv_sink import get_mpv_sink
            get_mpv_sink().stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

class TTSPlayer:
    """Wraps an engine with blocking speak() and a streaming sentence queue."""

    def __init__(self, engine):
        self._engine = engine
        self._playing = False
        self._lock = threading.Lock()
        # Streaming queue state. _generation is bumped by stop()/begin_stream()
        # so items queued before a barge-in are dropped by the worker.
        self._queue: _queue.Queue = _queue.Queue()
        self._generation = 0
        self._stream_first = True
        self._pending = 0
        self._idle = threading.Condition(self._lock)
        self._worker: threading.Thread | None = None

    @property
    def is_playing(self) -> bool:
        with self._lock:
            if self._playing or self._pending:
                return True
        try:
            from core.mpv_sink import get_mpv_sink
            return get_mpv_sink().is_playing()
        except Exception:
            return False

    def synthesize(self, text: str) -> bytes | None:
        """Audio bytes for text (no playback), when the engine supports it."""
        synth = getattr(self._engine, "synthesize", None)
        if synth is None or not text.strip():
            return None
        try:
            return synth(text)
        except Exception as e:
            print(f"[TTS] Synthesis error: {e}")
            return None

    def speak(
        self,
        text: str,
        on_start: Optional[Callable] = None,
        on_done: Optional[Callable] = None,
    ) -> None:
        """Synthesise and play text. BLOCKING – call from a dedicated thread.

        Bumps the generation first, so sentences still queued from an earlier
        turn cannot play after (or over) this one."""
        try:
            with self._lock:
                self._generation += 1
                self._playing = True
            if on_start:
                on_start()
            self._engine.speak(text)
        except Exception as e:
            print(f"[TTS] Error: {e}")
        finally:
            with self._lock:
                self._playing = False
            if on_done:
                on_done()

    def speak_chunk(self, text: str, append: bool = False) -> None:
        """Synthesise and play a single sentence chunk (blocking)."""
        if not text.strip():
            return
        try:
            timing_mark("first_speak_chunk", once=True)
            with self._lock:
                self._playing = True
            try:
                self._engine.speak(text, append=append)
            except TypeError:
                self._engine.speak(text)
        except Exception as e:
            print(f"[TTS Chunk] Error: {e}")
        finally:
            with self._lock:
                self._playing = False

    # -- streaming queue ----------------------------------------------------

    def begin_stream(self) -> None:
        """Start a new utterance: the next queued clip replaces mpv's playlist,
        and anything still queued from an earlier turn is dropped."""
        with self._lock:
            self._generation += 1
            self._stream_first = True

    def enqueue(
        self,
        text: str,
        play_local: bool = True,
        on_audio: Optional[Callable[[bytes | None], None]] = None,
    ) -> None:
        """Queue a sentence. Returns immediately.

        The worker synthesizes once; on_audio (if given) receives the bytes
        (or None when synthesis failed) before local playback starts.
        """
        if not text.strip():
            return
        with self._lock:
            self._pending += 1
            gen = self._generation
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._worker_loop, name="kurek-tts", daemon=True
                )
                self._worker.start()
        self._queue.put((gen, text, play_local, on_audio))

    def wait_stream(self, timeout: float = 60.0) -> bool:
        """Block until every queued sentence was synthesized and handed to the
        player. Returns False on timeout."""
        with self._idle:
            return self._idle.wait_for(lambda: self._pending == 0, timeout=timeout)

    def _worker_loop(self) -> None:
        while True:
            gen, text, play_local, on_audio = self._queue.get()
            try:
                with self._lock:
                    stale = gen != self._generation
                if not stale:
                    self._process(gen, text, play_local, on_audio)
            except Exception as e:
                print(f"[TTS Queue] Error: {e}")
            finally:
                with self._idle:
                    self._pending -= 1
                    self._idle.notify_all()

    def _process(self, gen: int, text: str, play_local: bool, on_audio) -> None:
        timing_mark("first_speak_chunk", once=True)
        audio = self.synthesize(text)
        if on_audio is not None:
            try:
                on_audio(audio)
            except Exception as e:
                print(f"[TTS Queue] on_audio callback error: {e}")
        if not play_local:
            return
        with self._lock:
            if gen != self._generation:
                return
            append = not self._stream_first
            self._stream_first = False
        if audio:
            _play_audio_bytes(audio, append=append)
            return
        # No bytes (engine without synthesize(), or cloud failure): speak
        # through the engine directly, blocking this worker until done.
        with self._lock:
            self._playing = True
        try:
            try:
                self._engine.speak(text, append=append)
            except TypeError:
                self._engine.speak(text)
        finally:
            with self._lock:
                self._playing = False

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            self._stream_first = True
        # Drop queued sentences; the worker's finally still decrements _pending
        # for the one it is processing.
        while True:
            try:
                self._queue.get_nowait()
            except _queue.Empty:
                break
            with self._idle:
                self._pending -= 1
                self._idle.notify_all()
        try:
            sd.stop()
        except Exception:
            pass
        if hasattr(self._engine, "stop"):
            try:
                self._engine.stop()
            except Exception:
                pass
        try:
            from core.mpv_sink import get_mpv_sink
            get_mpv_sink().stop()
        except Exception:
            pass
        with self._lock:
            self._playing = False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_tts_player(config: dict) -> TTSPlayer:
    """Build a player. tts_engine: "xai"/"grok" (default) or "system"
    ("mac_say"/"say" accepted as aliases)."""
    engine_name = (config.get("tts_engine") or "xai").lower()
    if engine_name in ("xai", "grok"):
        engine = XAITTSEngine(
            api_key=config.get("xai_api_key", ""),
            voice_id=config.get("tts_voice", "sal"),
        )
    else:
        engine = SystemSpeechEngine(voice=config.get("tts_voice", ""))
    return TTSPlayer(engine)
