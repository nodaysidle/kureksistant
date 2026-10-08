"""
Speech-to-Text engines for MARK XL.

Whisper  – offline transcription via faster-whisper (VAD-buffered)
Vosk     – offline streaming transcription (lighter)
"""
import json
import numpy as np


class WhisperSTT:
    """Offline transcription using faster-whisper."""

    def __init__(self, model_name: str = "base", language: str | None = None):
        import os
        from faster_whisper import WhisperModel
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
                    raise RuntimeError(
                        f"Whisper '{model_name}' model download failed.\n"
                        f"Internet access is required the first time to download the speech model (~75–290 MB).\n"
                        f"After the first download it runs fully offline.\n"
                        f"Details: {_dl_err}"
                    ) from _dl_err
            else:
                raise

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
            print(f"[STT] Transcription error: {e}")
            raise


class VoskSTT:
    """Streaming transcription using Vosk."""

    def __init__(self, model_path: str | None = None, language: str = "en-us"):
        from vosk import Model, KaldiRecognizer
        print("[STT] Loading Vosk model…")
        if model_path:
            model = Model(model_path)
        else:
            lang  = language.strip().lower() if language and language.strip().lower() != "auto" else "en-us"
            model = Model(lang=lang)
        self._rec = KaldiRecognizer(model, 16000)
        print("[STT] Vosk ready.")

    def process_chunk(self, audio_bytes: bytes) -> tuple[str, bool]:
        """Feed raw int16 LE PCM bytes. Returns (text, is_final)."""
        if self._rec.AcceptWaveform(audio_bytes):
            result = json.loads(self._rec.Result())
            return result.get("text", ""), True
        partial = json.loads(self._rec.PartialResult())
        return partial.get("partial", ""), False


class DeepgramSTT:
    """Ultra-low latency cloud STT via Deepgram Nova-2 / Nova-3."""

    def __init__(self, api_key: str | None = None, model: str = "nova-2"):
        import os
        from memory.config_manager import get_deepgram_key
        self.api_key = api_key or get_deepgram_key() or os.environ.get("DEEPGRAM_API_KEY", "")
        self.model = model

    def transcribe(self, audio_data, sample_rate: int = 16000) -> str:
        """Transcribe PCM int16 or WAV bytes via Deepgram."""
        import requests
        import io
        import wave

        if not self.api_key:
            print("[STT] ⚠️ DEEPGRAM_API_KEY is not set.")
            return ""

        # Convert numpy or raw bytes to WAV in memory
        if isinstance(audio_data, np.ndarray):
            clean_audio = audio_data - np.mean(audio_data)
            peak = float(np.max(np.abs(clean_audio)))
            if peak > 1e-4:
                clean_audio = (clean_audio / peak) * 0.95
            else:
                clean_audio = audio_data

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                # Convert float32 to int16 if needed
                if clean_audio.dtype in (np.float32, np.float64):
                    scaled = (np.clip(clean_audio, -1.0, 1.0) * 32767).astype(np.int16)
                    wf.writeframes(scaled.tobytes())
                else:
                    wf.writeframes(clean_audio.tobytes())
            payload = buf.getvalue()
        elif isinstance(audio_data, bytes):
            payload = audio_data
        else:
            return ""

        url = f"https://api.deepgram.com/v1/listen?model={self.model}&smart_format=true"
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "audio/wav",
        }
        try:
            resp = requests.post(url, headers=headers, data=payload, timeout=8)
            if resp.status_code == 200:
                res = resp.json()
                channels = res.get("results", {}).get("channels", [])
                if channels and channels[0].get("alternatives"):
                    return channels[0]["alternatives"][0].get("transcript", "").strip()
            else:
                print(f"[Deepgram] HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"[Deepgram] Error: {e}")
        return ""

