"""Chunks in, transcript segments out. One worker serves both tracks in arrival order."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .. import config
from ..audio import wav
from ..config import SAMPLE_RATE as SR
from .base import Backend, Word, looks_hallucinated
from .chunker import Chunk, Chunker
from .vad import SileroVAD


log = logging.getLogger(__name__)


@dataclass
class Segment:
    track: str
    start: float
    end: float
    text: str
    words: list[Word]
    chunk_start: float
    chunk_end: float
    speech_end: float
    lag: float | None = None  # seconds between end of speech and the text appearing
    backend: str = ""
    id: int = 0
    language: str | None = None  # the chosen language, or the one detected in automatic mode

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "track": self.track,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "words": [w.to_json() for w in self.words],
            "chunk": [round(self.chunk_start, 3), round(self.chunk_end, 3)],
            "speech_end": round(self.speech_end, 3),
            "lag": None if self.lag is None else round(self.lag, 2),
            "backend": self.backend,
            "language": self.language,
        }

    @classmethod
    def from_json(cls, d: dict) -> "Segment":
        return cls(
            track=d["track"], start=d["start"], end=d["end"], text=d["text"],
            words=[Word.from_json(w) for w in d.get("words", [])],
            chunk_start=d["chunk"][0], chunk_end=d["chunk"][1],
            speech_end=d.get("speech_end", d["end"]), lag=d.get("lag"),
            backend=d.get("backend", ""), id=d.get("id", 0), language=d.get("language"),
        )


def segment_from(chunk: Chunk, words: list[Word], backend_name: str, prompt: str = "") -> Segment | None:
    """Place a chunk's words on the session timeline; keep only the chunk's own share."""
    lo = chunk.valid_start if chunk.valid_start > chunk.start_s + 1e-6 else float("-inf")
    hi = chunk.valid_end if chunk.valid_end < chunk.end_s - 1e-6 else float("inf")
    kept = []
    for w in words:
        a, b = w.start + chunk.start_s, w.end + chunk.start_s
        if lo <= (a + b) / 2 < hi:
            kept.append(Word(a, b, w.text))
    if not kept:
        return None
    text = " ".join(w.text for w in kept)
    if looks_hallucinated(text, chunk.speech_seconds, prompt):
        return None
    return Segment(
        track=chunk.track, start=kept[0].start, end=kept[-1].end, text=text, words=kept,
        chunk_start=chunk.start_s, chunk_end=chunk.end_s, speech_end=chunk.speech_end, backend=backend_name,
    )


class LanguagePicker:
    """Which language to ask for, per chunk.

    A fixed language is used as is. In automatic mode Whisper detects the language
    of each chunk, but its detection is unreliable on a second or two of speech
    ("ja", "okay"): short chunks reuse the language last detected on that track.
    """

    SHORT = 3.0  # seconds of speech below which detection is not trusted

    def __init__(self, language: str = "en"):
        self.language = language
        self._by_track: dict[str, str] = {}
        self._latest: str | None = None

    def choose(self, chunk: Chunk) -> str:
        if self.language != config.AUTO:
            return self.language
        if chunk.speech_seconds < self.SHORT:
            return self._by_track.get(chunk.track) or self._latest or config.AUTO
        return config.AUTO

    def learn(self, chunk: Chunk, detected: str | None) -> None:
        if self.language == config.AUTO and detected and chunk.speech_seconds >= self.SHORT:
            self._by_track[chunk.track] = self._latest = detected


PromptFor = Callable[[str], str]  # language code (or "auto") -> Whisper prompt


def run_chunk(backend: Backend, chunk: Chunk, picker: LanguagePicker, prompt_for: PromptFor) -> Segment | None:
    language = picker.choose(chunk)
    prompt = prompt_for(language)
    words = backend.transcribe(chunk.audio, prompt, language)
    spoken = language if language != config.AUTO else getattr(backend, "last_language", None)
    picker.learn(chunk, spoken)
    seg = segment_from(chunk, words, backend.name, prompt)
    if seg is not None:
        seg.language = spoken
    return seg


class Transcriber:
    """Live: attach to capture tracks. Chunks queue up and are transcribed in order."""

    def __init__(
        self,
        backend: Backend,
        on_segment: Callable[[Segment], None],
        prompt: PromptFor = lambda language: "",
        language: str = "en",
        session_time: Callable[[], float] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        self.backend = backend
        self.on_segment = on_segment
        self.prompt = prompt
        self.picker = LanguagePicker(language)
        self.session_time = session_time
        self.on_error = on_error or (lambda msg: None)
        self._queue: queue.Queue[Chunk | None] = queue.Queue()
        self._chunkers: dict[str, Chunker] = {}
        self._worker = threading.Thread(target=self._run, daemon=True, name="aku-asr")
        self._worker.start()
        self.lags: list[float] = []
        self.failed = 0

    def chunker(self, track: str) -> Chunker:
        c = Chunker(track, SileroVAD(config.ensure_vad_model()), self._queue.put)
        self._chunkers[track] = c
        return c

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def _transcribe(self, chunk: Chunk) -> Segment | None:
        last: Exception | None = None
        for _ in range(2):  # one retry: the local server restarts itself if it died
            try:
                return run_chunk(self.backend, chunk, self.picker, self.prompt)
            except Exception as e:  # noqa: BLE001 - any backend failure is reported, not fatal
                last = e
                time.sleep(0.5)
        self.failed += 1
        self.on_error(f"Transcription failed for {chunk.start_s:.0f}s-{chunk.end_s:.0f}s ({chunk.track}): {last}")
        return None

    def _run(self) -> None:
        while True:
            chunk = self._queue.get()
            if chunk is None:
                return
            seg = self._transcribe(chunk)
            if seg is not None:
                if self.session_time is not None:
                    seg.lag = max(0.0, self.session_time() - seg.speech_end)
                    self.lags.append(seg.lag)
                try:
                    self.on_segment(seg)
                except Exception as e:  # noqa: BLE001 - e.g. the disk is full; keep going, the audio has it
                    log.exception("could not store a transcribed segment")
                    self.failed += 1
                    self.on_error(f"Could not save part of the transcript: {e}")

    def finish(self) -> None:
        """Close open chunks, transcribe what is queued, stop the worker."""
        for c in self._chunkers.values():
            c.flush()
        self._queue.put(None)
        self._worker.join()


def transcribe_file(
    path: Path,
    track: str,
    backend: Backend,
    on_segment: Callable[[Segment], None],
    prompt: PromptFor = lambda language: "",
    language: str | LanguagePicker = "en",
    start_sample: int = 0,
    progress: Callable[[float], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> int:
    """Offline pass over a WAV (recovery, quality pass). Returns the number of segments.

    Pass one LanguagePicker for both tracks so automatic mode can share what it learned.
    """
    total = wav.frames(path)
    count = 0
    errors = on_error or (lambda msg: None)
    picker = language if isinstance(language, LanguagePicker) else LanguagePicker(language)

    def handle(chunk: Chunk) -> None:
        nonlocal count
        try:
            seg = run_chunk(backend, chunk, picker, prompt)
        except Exception as e:  # noqa: BLE001
            errors(f"Transcription failed for {chunk.start_s:.0f}s-{chunk.end_s:.0f}s ({track}): {e}")
            return
        if seg is not None:
            count += 1
            on_segment(seg)

    chunker = Chunker(track, SileroVAD(config.ensure_vad_model()), handle)
    chunker.pos = chunker._frame_pos = chunker._buf_start = chunker._floor = start_sample
    pos = start_sample
    block = SR * 5
    while pos < total:
        pcm = wav.read(path, pos, block)
        if len(pcm) == 0:
            break
        chunker.feed(pcm, pos)
        pos += len(pcm)
        if progress:
            progress(pos / total)
    chunker.flush()
    return count
