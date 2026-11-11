"""Cut a continuous track into chunks at pauses, for transcription.

A chunk closes after a pause, or at MAX_LEN. A forced cut lands on the
quietest frame of the recent audio and the two chunks overlap slightly; each
chunk has a "valid" time range so words in the overlap are kept exactly once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..config import SAMPLE_RATE as SR
from .vad import FRAME


@dataclass
class Chunk:
    track: str
    start: int  # first sample, in session samples
    audio: np.ndarray  # int16
    valid_start: float  # seconds; words whose midpoint is in [valid_start, valid_end) belong here
    valid_end: float
    speech_end: float  # seconds; when the speech in this chunk ended
    speech_seconds: float

    @property
    def start_s(self) -> float:
        return self.start / SR

    @property
    def end_s(self) -> float:
        return (self.start + len(self.audio)) / SR


class Chunker:
    THRESHOLD = 0.5  # speech starts
    KEEP = 0.35  # speech continues (hysteresis)
    MIN_SILENCE = int(0.6 * SR)
    PRE_ROLL = int(0.25 * SR)
    POST_ROLL = int(0.25 * SR)
    MAX_LEN = int(12.0 * SR)
    MIN_CUT = int(4.0 * SR)  # a forced cut is never earlier than this into the chunk
    OVERLAP = int(0.2 * SR)
    MIN_SPEECH = 0.25  # seconds
    CLEAR_PAUSE = 0.15  # VAD probability that counts as a real pause

    def __init__(self, track: str, vad: Callable[[np.ndarray], float], on_chunk: Callable[[Chunk], None]):
        self.track = track
        self.vad = vad
        self.on_chunk = on_chunk
        self.pos = 0  # next expected sample
        self._buf = np.zeros(0, dtype=np.int16)
        self._buf_start = 0
        self._frame_pos = 0  # start of the next VAD frame
        self._active = False
        self._chunk_start = 0
        self._valid_start = 0
        self._last_speech_end = 0
        self._speech_frames = 0
        self._probs: list[tuple[int, float]] = []  # (frame start, prob) inside the open chunk
        self._floor = 0  # no chunk may start before this sample

    def feed(self, pcm: np.ndarray, start: int | None = None) -> None:
        if start is not None and start != self.pos:
            # The clocked writer never skips, but be safe: treat a jump as silence.
            gap = start - self.pos
            if gap > 0:
                pcm = np.concatenate([np.zeros(gap, dtype=np.int16), pcm])
        self._buf = np.concatenate([self._buf, pcm])
        self.pos += len(pcm)
        while self._frame_pos + FRAME <= self.pos:
            a = self._frame_pos - self._buf_start
            self._frame(self._frame_pos, self.vad(self._buf[a : a + FRAME]))
            self._frame_pos += FRAME
        if not self._active:
            keep_from = max(self._buf_start, self._frame_pos - self.PRE_ROLL)
            self._buf = self._buf[keep_from - self._buf_start :]
            self._buf_start = keep_from

    def _frame(self, fpos: int, prob: float) -> None:
        fend = fpos + FRAME
        if not self._active:
            if prob >= self.THRESHOLD:
                self._active = True
                self._chunk_start = max(self._buf_start, fpos - self.PRE_ROLL, self._floor)
                self._valid_start = self._chunk_start
                self._last_speech_end = fend
                self._speech_frames = 1
                self._probs = [(fpos, prob)]
            return
        self._probs.append((fpos, prob))
        if prob >= self.KEEP:
            self._last_speech_end = fend
            if prob >= self.THRESHOLD:
                self._speech_frames += 1
        if fend - self._last_speech_end >= self.MIN_SILENCE:
            self._close(min(self._last_speech_end + self.POST_ROLL, fend))
        elif fend - self._chunk_start >= self.MAX_LEN:
            self._cut(fend)

    def _emit(self, end: int, valid_end: int) -> None:
        speech = self._speech_frames * FRAME / SR
        if speech < self.MIN_SPEECH:
            return
        a = self._chunk_start - self._buf_start
        b = end - self._buf_start
        self.on_chunk(
            Chunk(
                track=self.track,
                start=self._chunk_start,
                audio=self._buf[a:b].copy(),
                valid_start=self._valid_start / SR,
                valid_end=valid_end / SR,
                speech_end=min(self._last_speech_end, end) / SR,
                speech_seconds=speech,
            )
        )

    def _close(self, end: int) -> None:
        self._emit(end, end)
        self._active = False
        self._floor = end
        self._probs = []

    def _cut(self, now_end: int) -> None:
        earliest = self._chunk_start + self.MIN_CUT
        cut_pos, cut_prob = None, 2.0
        for fpos, prob in self._probs:
            if fpos < earliest:
                continue
            # prefer the latest clear pause; failing that, the quietest frame
            if prob < self.CLEAR_PAUSE or prob <= cut_prob:
                cut_pos, cut_prob = fpos, min(prob, self.CLEAR_PAUSE) if prob < self.CLEAR_PAUSE else prob
        if cut_pos is None:
            cut_pos = now_end - FRAME
        cut = cut_pos + FRAME // 2
        self._emit(min(cut + self.OVERLAP, now_end), cut)
        # continue in a new chunk that overlaps the old one a little
        self._chunk_start = max(cut - self.OVERLAP, self._chunk_start)
        self._valid_start = cut
        self._probs = [(f, p) for f, p in self._probs if f >= self._chunk_start]
        self._speech_frames = sum(1 for _, p in self._probs if p >= self.THRESHOLD)
        keep_from = self._chunk_start
        self._buf = self._buf[keep_from - self._buf_start :]
        self._buf_start = keep_from

    def flush(self) -> None:
        """End of the track: close whatever is open."""
        if self._active:
            self._close(self.pos)
