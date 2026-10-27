"""
Speech-to-Text engines for Kurek.

WhisperSTT     offline transcription via faster-whisper
DeepgramSTT    cloud STT via Deepgram Nova-2 / Nova-3
OpenRouterSTT  cloud STT via OpenRouter audio transcriptions
FallbackSTT    wraps a primary engine and lazily falls back on STTError

Every engine returns "" only when transcription succeeded but heard no speech;
any failure (missing key, HTTP error, timeout, network, local model error)
raises STTError.
"""
import io
import threading
import wave
from typing import Callable

import numpy as np


class STTError(Exception):
    """Transcription failed (as opposed to succeeding with no speech)."""


def _to_wav_bytes(audio_data, sample_rate: int) -> bytes:
    """Normalize a float/int numpy buffer (or pass through bytes) into WAV bytes."""
    if isinstance(audio_data, (bytes, bytearray)):
        return bytes(audio_data)
    if not isinstance(audio_data, np.ndarray):
        raise STTError(f"unsupported audio type: {type(audio_data).__name__}")

    clean_audio = audio_data - np.mean(audio_data)
    peak = float(np.max(np.abs(clean_audio))) if clean_audio.size else 0.0
    if peak > 1e-4:
        clean_audio = (clean_audio / peak) * 0.95
    else:
        clean_audio = audio_data

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        if clean_audio.dtype in (np.float32, np.float64):
            scaled = (np.clip(clean_audio, -1.0, 1.0) * 32767).astype(np.int16)
            wf.writeframes(scaled.tobytes())
        else:
            wf.writeframes(clean_audio.astype(np.int16).tobytes())
    return buf.getvalue()


def _post(label: str, url: str, timeout: float, **kwargs):
    """requests.post that maps every transport/HTTP failure to STTError."""
    import requests

    try:
        resp = requests.post(url, timeout=timeout, **kwargs)
    except requests.Timeout as e:
        raise STTError(f"{label} timeout: {e}") from e
    except Exception as e:
        raise STTError(f"{label} network error: {e}") from e
    if resp.status_code != 200:
        detail = (getattr(resp, "text", "") or "")[:200]
        print(f"[STT] {label} HTTP {resp.status_code}: {detail}", flush=True)
        raise STTError(f"{label} HTTP {resp.status_code}")
    try:
        return resp.json()
    except Exception as e:
        raise STTError(f"{label} invalid JSON response: {e}") from e


class WhisperSTT:
    """Offline transcription using faster-whisper."""

    def __init__(self, model_name: str = "base.en", language: str | None = None):
        import os
        try:
            from faster_whisper import WhisperModel
        except Exception as e:
            raise STTError(f"faster-whisper is not available: {e}") from e
        print(f"[STT] Loading Whisper '{model_name}'…")
        try:
            import torch
            device  = "cuda" if torch.cuda.is_available() else "cpu"
            compute = "float16" if device == "cuda" else "int8"
        except Exception:
            device, compute = "cpu", "int8"

        try:
            self._model = WhisperModel(model_name, device=device, compute_type=compute)
        except Exception as _first_err:
            # Offline flag set but model not cached yet → clear flags and download once.
            # Keywords cover multiple huggingface_hub error message variants across versions.
            _e = str(_first_err).lower()
            _offline_keywords = (
                "offline", "not found", "cache", "localentry",
                "does not exist", "outgoing", "local_files_only",
            )
            if any(k in _e for k in _offline_keywords):
                print(f"[STT] Whisper '{model_name}' not in local cache — downloading (one-time, internet required)…")
                os.environ.pop("HF_HUB_OFFLINE",      None)
                os.environ.pop("TRANSFORMERS_OFFLINE", None)
                os.environ.pop("HF_DATASETS_OFFLINE",  None)
                try:
                    self._model = WhisperModel(model_name, device=device, compute_type=compute)
                except Exception as _dl_err:
                    raise STTError(
                        f"Whisper '{model_name}' model download failed.\n"
                        f"Internet access is required the first time to download the speech model (~75–290 MB).\n"
                        f"After the first download it runs fully offline.\n"
                        f"Details: {_dl_err}"
                    ) from _dl_err
            else:
                raise STTError(f"Whisper '{model_name}' failed to load: {_first_err}") from _first_err

        self._language = None if (not language or language.strip().lower() == "auto") else language.strip().lower()
        print(f"[STT] Whisper '{model_name}' ready ({device})")

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, **kwargs) -> str:
        """Transcribe a float32 mono 16 kHz numpy array. Returns transcript string."""
        try:
            # 1. Strip DC offset (critical for many USB / desk mics)
            clean_audio = audio - np.mean(audio)
            # 2. Peak normalize to 0.95 so low-gain microphone audio is clear to Whisper
            peak = float(np.max(np.abs(clean_audio)))
            if peak > 1e-4:
                clean_audio = (clean_audio / peak) * 0.95

            segments, _ = self._model.transcribe(
                clean_audio,
                language=self._language,
                beam_size=1,                       # greedy — 2-3x faster
                best_of=1,
                condition_on_previous_text=False,  # no hallucinations, faster
                vad_filter=False,                  # Do not drop user speech chunks
            )
            return " ".join(s.text for s in segments).strip()
        except Exception as e:
            print(f"[STT] Transcription error: {e}", flush=True)
            raise STTError(f"Whisper transcription failed: {e}") from e


class DeepgramSTT:
    """Ultra-low latency cloud STT via Deepgram Nova-2 / Nova-3."""

    def __init__(self, api_key: str | None = None, model: str = "nova-2", language: str | None = None):
        import os
        from memory.config_manager import get_deepgram_key
        self.api_key = api_key or get_deepgram_key() or os.environ.get("DEEPGRAM_API_KEY", "")
        self.model = model
        pinned = (language or "").strip().lower()
        self.language = None if pinned in ("", "auto") else pinned

    def transcribe(self, audio_data, sample_rate: int = 16000) -> str:
        """Transcribe a numpy buffer or WAV bytes via Deepgram. Raises STTError on failure."""
        if not self.api_key:
            raise STTError("Deepgram STT: DEEPGRAM_API_KEY is not set")
        payload = _to_wav_bytes(audio_data, sample_rate)
        url = f"https://api.deepgram.com/v1/listen?model={self.model}&smart_format=true"
        if self.language:
            url += f"&language={self.language}"
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "audio/wav",
        }
        res = _post("Deepgram STT", url, 8, headers=headers, data=payload)
        channels = (res.get("results") or {}).get("channels") or []
        if channels and channels[0].get("alternatives"):
            return (channels[0]["alternatives"][0].get("transcript") or "").strip()
        return ""


class OpenRouterSTT:
    """Cloud STT via OpenRouter audio transcriptions (default: openai/whisper-large-v3-turbo)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "openai/whisper-large-v3-turbo",
        language: str | None = None,
    ):
        import os
        from memory.config_manager import get_openrouter_api_key
        self.api_key = api_key or get_openrouter_api_key() or os.environ.get("OPENROUTER_API_KEY", "")
        self.model = model
        self.language = language

    def transcribe(self, audio_data, sample_rate: int = 16000) -> str:
        """Transcribe a numpy buffer or WAV bytes via OpenRouter. Raises STTError on failure."""
        import base64

        if not self.api_key:
            raise STTError("OpenRouter STT: OPENROUTER_API_KEY is not set")
        raw_bytes = _to_wav_bytes(audio_data, sample_rate)
        url = "https://openrouter.ai/api/v1/audio/transcriptions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "input_audio": {
                "data": base64.b64encode(raw_bytes).decode("utf-8"),
                "format": "wav",
            },
        }
        if self.language:
            payload["language"] = self.language
        res = _post("OpenRouter STT", url, 15, headers=headers, json=payload)
        return (res.get("text") or "").strip()


class FallbackSTT:
    """Use ``primary``; on STTError switch to a lazily built fallback engine.

    The fallback is constructed at most once (thread-safe) and then reused for
    every later failure of the primary. A fallback construction or
    transcription failure is raised as STTError.
    """

    def __init__(self, primary, fallback_factory: Callable[[], object]):
        self.primary = primary
        self._fallback_factory = fallback_factory
        self._fallback = None
        self._lock = threading.Lock()

    def _get_fallback(self):
        if self._fallback is not None:
            return self._fallback
        with self._lock:
            if self._fallback is None:
                try:
                    self._fallback = self._fallback_factory()
                except STTError:
                    raise
                except Exception as e:
                    raise STTError(f"fallback STT init failed: {e}") from e
            return self._fallback

    def transcribe(self, audio, sample_rate: int = 16000) -> str:
        try:
            return self.primary.transcribe(audio, sample_rate=sample_rate)
        except STTError as e:
            print(f"[STT] Primary STT failed ({e}); using fallback", flush=True)
        fallback = self._get_fallback()
        try:
            return fallback.transcribe(audio, sample_rate=sample_rate)
        except STTError:
            raise
        except Exception as e:
            raise STTError(f"fallback STT failed: {e}") from e
