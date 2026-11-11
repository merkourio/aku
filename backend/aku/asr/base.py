"""ASR data types and the backend interface."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass
class Word:
    start: float
    end: float
    text: str

    def to_json(self) -> dict:
        return {"s": round(self.start, 3), "e": round(self.end, 3), "w": self.text}

    @classmethod
    def from_json(cls, d: dict) -> "Word":
        return cls(d["s"], d["e"], d["w"])


class Backend(Protocol):
    name: str
    word_timestamps: bool

    def transcribe(self, audio: np.ndarray, prompt: str = "", language: str = "en") -> list[Word]:
        """Transcribe 16 kHz mono int16 audio. Word times are seconds from the start of `audio`."""
        ...

    def close(self) -> None: ...


_NON_SPEECH = re.compile(r"^\s*[\[(♪*].*[\])♪*]\s*$")
# Whisper's stock phrases when it is handed near-silence.
_SILENCE_PHRASES = {"thank you.", "thanks for watching!", "thank you for watching.", "you", "bye.", "."}


def is_non_speech(text: str) -> bool:
    """Tags like [BLANK_AUDIO] or (music) that Whisper emits for non-speech."""
    return bool(_NON_SPEECH.match(text)) or not text.strip()


def looks_hallucinated(text: str, speech_seconds: float) -> bool:
    return speech_seconds < 0.6 and text.strip().lower() in _SILENCE_PHRASES


def tokens_to_words(tokens: list[tuple[float, float, str]]) -> list[Word]:
    """Merge sub-word tokens: a token starting with a space begins a new word."""
    words: list[Word] = []
    for start, end, text in tokens:
        if not text:
            continue
        if text[0].isspace() or not words:
            if text.strip():
                words.append(Word(start, max(end, start), text.strip()))
        else:
            w = words[-1]
            w.text += text.rstrip()
            w.end = max(w.end, end)
    return words


def spread_words(text: str, start: float, end: float) -> list[Word]:
    """Even word timings for a backend that returns text only."""
    parts = text.split()
    if not parts:
        return []
    total = sum(len(p) + 1 for p in parts)
    out, t = [], start
    for p in parts:
        d = (end - start) * (len(p) + 1) / total
        out.append(Word(t, t + d, p))
        t += d
    return out


def tidy(words: list[Word], duration: float) -> list[Word]:
    """Clamp to the chunk and keep times in order."""
    last = 0.0
    for w in words:
        w.start = min(max(w.start, last, 0.0), duration)
        w.end = min(max(w.end, w.start), duration)
        last = w.start
    return words
