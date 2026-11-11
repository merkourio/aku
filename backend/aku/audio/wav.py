"""16-bit mono WAV files that survive a crash: append-only data, header fixed on close or repair."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

HEADER_SIZE = 44


def _header(sample_rate: int, data_bytes: int) -> bytes:
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_bytes,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        1,  # mono
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_bytes,
    )


class WavWriter:
    def __init__(self, path: Path, sample_rate: int = 16000):
        self.path = Path(path)
        self.sample_rate = sample_rate
        self.frames = 0
        self._f = open(self.path, "wb")
        self._f.write(_header(sample_rate, 0))
        self._f.flush()

    def write(self, pcm: np.ndarray) -> None:
        if len(pcm) == 0:
            return
        self._f.write(np.ascontiguousarray(pcm, dtype="<i2").tobytes())
        self.frames += len(pcm)

    def flush(self) -> None:
        if not self._f.closed:
            self._f.flush()

    def close(self) -> None:
        if self._f.closed:
            return
        self._f.seek(0)
        self._f.write(_header(self.sample_rate, self.frames * 2))
        self._f.close()


def repair(path: Path, sample_rate: int = 16000) -> int:
    """Rewrite the header from the file length. Returns the number of frames."""
    path = Path(path)
    size = path.stat().st_size
    if size < HEADER_SIZE:
        with open(path, "wb") as f:
            f.write(_header(sample_rate, 0))
        return 0
    data_bytes = (size - HEADER_SIZE) // 2 * 2
    with open(path, "r+b") as f:
        f.write(_header(sample_rate, data_bytes))
        f.truncate(HEADER_SIZE + data_bytes)
    return data_bytes // 2


def frames(path: Path) -> int:
    return max(0, (Path(path).stat().st_size - HEADER_SIZE) // 2)


def read(path: Path, start: int = 0, count: int | None = None) -> np.ndarray:
    """Read int16 samples [start, start+count) straight from the data area."""
    total = frames(path)
    start = max(0, min(start, total))
    count = total - start if count is None else max(0, min(count, total - start))
    with open(path, "rb") as f:
        f.seek(HEADER_SIZE + start * 2)
        return np.frombuffer(f.read(count * 2), dtype="<i2").copy()


def encode(pcm: np.ndarray, sample_rate: int = 16000) -> bytes:
    """An in-memory WAV file."""
    data = np.ascontiguousarray(pcm, dtype="<i2").tobytes()
    return _header(sample_rate, len(data)) + data
