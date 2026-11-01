"""core/vocabulary.py — the user's own word list, applied to what the microphone heard.

Reads reverseduo's dictionary.json in place and read-only (one fact, one store):
its `replacements` fix phrases the speech engine gets wrong ("no days idle" ->
"nodaysidle"). Corrections run on every transcript whatever engine produced it.
A missing or unreadable file leaves the transcript unchanged; nothing here
raises into the listen loop.

Its `vocabulary` list is not used: keyword hints reach only Deepgram, which is
not the active engine (OpenRouter Whisper, whose prompt option is
provider-specific), and a Whisper prompt can echo its own words from silence.

KUREK_VOCAB_FILE overrides the path; set it to an empty value to turn this off.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config" / "reverseduo" / "dictionary.json"

# Terms Kurek's own commands and answers depend on: never rewrite them alone.
_COMMAND_WORDS = frozenset({
    "yes", "no", "ok", "okay", "yeah", "sure", "cancel", "stop", "close", "open", "delete",
    "save", "send", "confirm", "start", "fresh",
})


@dataclass(frozen=True)
class _Vocab:
    pattern: re.Pattern | None = None
    targets: dict[str, str] = field(default_factory=dict)


_state: tuple[tuple | None, _Vocab] = (None, _Vocab())  # (path, mtime) the vocab was built from


def _path() -> Path | None:
    raw = os.environ.get("KUREK_VOCAB_FILE")
    if raw is None:
        return DEFAULT_PATH
    return Path(raw).expanduser() if raw.strip() else None


def _keep_replacement(original: str, target: str) -> bool:
    """Drop entries a dictation app needs but a voice assistant must not apply."""
    words = original.lower().split()
    if not words or not original.isascii() or original.lower() == target.lower():
        return False
    if target.startswith(("/", "~")) or "/Users/" in target or "/Volumes/" in target:
        return False
    if not any(ch.isalnum() for ch in target):  # "slash" -> "/" corrupts ordinary sentences
        return False
    if len(words) == 1 and (len(original) <= 3 or words[0] in _COMMAND_WORDS):
        return False
    return True


def _build(data: dict) -> _Vocab:
    targets: dict[str, str] = {}
    for entry in data.get("replacements") or []:
        original, target = str(entry.get("original", "")).strip(), str(entry.get("with", "")).strip()
        if _keep_replacement(original, target):
            targets.setdefault(original.lower(), target)
    pattern = None
    if targets:
        # Longest first, so "no days idle architect" beats "no days idle"; one pass, so no output is re-replaced.
        alternation = "|".join(re.escape(k) for k in sorted(targets, key=len, reverse=True))
        pattern = re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)
    return _Vocab(pattern, targets)


def _load() -> _Vocab:
    global _state
    path = _path()
    if path is None:
        return _Vocab()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    key = (str(path), mtime)
    if _state[0] == key:
        return _state[1]
    vocab = _Vocab()
    if mtime is not None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            vocab = _build(data if isinstance(data, dict) else {})
        except (OSError, ValueError, AttributeError) as e:
            print(f"[Kurek Vocab] notice: can't read {path.name}: {e}", flush=True)
    _state = (key, vocab)
    return vocab


def correct(text: str) -> str:
    """The transcript with the user's replacements applied; unchanged when there is nothing to apply."""
    try:
        vocab = _load()
        if not text or vocab.pattern is None:
            return text
        return vocab.pattern.sub(lambda m: vocab.targets[m.group(0).lower()], text)
    except Exception as e:
        print(f"[Kurek Vocab] notice: {e}", flush=True)
        return text
