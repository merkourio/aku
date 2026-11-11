"""Chunks in, transcript segments out. One worker serves both tracks in arrival order."""

from __future__ import annotations

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
        }

    @classmethod
    def from_json(cls, d: dict) -> "Segment":
        return cls(
            track=d["track"], start=d["start"], end=d["end"], text=d["text"],
            words=[Word.from_json(w) for w in d.get("words", [])],
            chunk_start=d["chunk"][0], chunk_end=d["chunk"][1],
            speech_end=d.get("speech_end", d["end"]), lag=d.get("lag"),
            backend=d.get("backend", ""), id=d.get("id", 0),
        )


def segment_from(chunk: Chunk, words: list[Word], backend_name: str) -> Segment | None:
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
    if looks_hallucinated(text, chunk.speech_seconds):
        return None
    return Segment(
        track=chunk.track, start=kept[0].start, end=kept[-1].end, text=text, words=kept,
        chunk_start=chunk.start_s, chunk_end=chunk.end_s, speech_end=chunk.speech_end, backend=backend_name,
    )


class Transcriber:
    """Live: attach to capture tracks. Chunks queue up and are transcribed in order."""

    def __init__(
        self,
        backend: Backend,
        on_segment: Callable[[Segment], None],
        prompt: Callable[[], str] = lambda: "",
        language: str = "en",
        session_time: Callable[[], float] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        self.backend = backend
        self.on_segment = on_segment
        self.prompt = prompt
        self.language = language
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
                words = self.backend.transcribe(chunk.audio, self.prompt(), self.language)
                return segment_from(chunk, words, self.backend.name)
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
                self.on_segment(seg)

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
    prompt: str = "",
    language: str = "en",
    start_sample: int = 0,
    progress: Callable[[float], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> int:
    """Offline pass over a WAV (recovery, quality pass). Returns the number of segments."""
    total = wav.frames(path)
    count = 0
    errors = on_error or (lambda msg: None)

    def handle(chunk: Chunk) -> None:
        nonlocal count
        try:
            words = backend.transcribe(chunk.audio, prompt, language)
        except Exception as e:  # noqa: BLE001
            errors(f"Transcription failed for {chunk.start_s:.0f}s-{chunk.end_s:.0f}s ({track}): {e}")
            return
        seg = segment_from(chunk, words, backend.name)
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
