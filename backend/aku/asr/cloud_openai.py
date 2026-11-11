"""Cloud backend: OpenAI transcription API. Transcription only; speakers are always identified locally.

whisper-1 returns word timestamps (needed to split a line where the speaker changes).
The gpt-4o-transcribe models return text only, so their words get evenly spread timings
and each chunk tends to go to one speaker whole.
"""

from __future__ import annotations

import numpy as np

from ..audio import wav
from ..config import SAMPLE_RATE as SR
from .base import Word, is_non_speech, spread_words, tidy

WORD_TIMESTAMP_MODELS = {"whisper-1"}


class OpenAIBackend:
    def __init__(self, api_key: str, model: str = "whisper-1", client=None):
        if not api_key and client is None:
            raise RuntimeError("No OpenAI API key set. Add one in Settings.")
        self.model = model
        self.name = f"openai:{model}"
        self.word_timestamps = model in WORD_TIMESTAMP_MODELS
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=120, max_retries=2)
        self._client = client

    def transcribe(self, audio: np.ndarray, prompt: str = "", language: str = "en") -> list[Word]:
        duration = len(audio) / SR
        file = ("chunk.wav", wav.encode(audio), "audio/wav")
        if self.word_timestamps:
            kwargs = {"prompt": prompt} if prompt else {}
            r = self._client.audio.transcriptions.create(
                model=self.model, file=file, language=language, temperature=0,
                response_format="verbose_json", timestamp_granularities=["word"], **kwargs,
            )
            words = [Word(float(w.start), float(w.end), str(w.word).strip()) for w in (getattr(r, "words", None) or [])]
            words = [w for w in words if w.text]
            text = (getattr(r, "text", "") or "").strip()
            if is_non_speech(text):
                return []
            if not words:
                return spread_words(text, 0.0, duration)
            return tidy(_restore_punctuation(words, text), duration)
        r = self._client.audio.transcriptions.create(model=self.model, file=file, language=language, response_format="json")
        text = (getattr(r, "text", "") or "").strip()
        return [] if is_non_speech(text) else spread_words(text, 0.0, duration)

    def close(self) -> None:
        pass


def _restore_punctuation(words: list[Word], text: str) -> list[Word]:
    """whisper-1's word list drops punctuation; the full text has it. Re-attach where the words line up."""
    parts = text.split()
    strip = lambda s: "".join(c for c in s.lower() if c.isalnum())
    if len(parts) != len(words) or any(strip(p) != strip(w.text) for p, w in zip(parts, words)):
        return words
    for p, w in zip(parts, words):
        w.text = p
    return words
