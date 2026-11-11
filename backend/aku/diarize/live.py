"""Live speaker separation during a call.

As each stretch of the *them* track is transcribed, its speech is embedded (by a
helper process that holds the voice model) and all embeddings so far are clustered
again. Speaker numbers stay stable from one update to the next, and the number of
speakers can be set or changed at any time.

This always uses the built-in method. pyannote, when configured, still does the
full pass after the call.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import sys
import threading
from collections import Counter
from pathlib import Path
from typing import Callable

import numpy as np

from .. import config
from ..asr.vad import SileroVAD
from ..audio import wav
from ..config import SAMPLE_RATE as SR
from . import cluster


class EmbedWorker:
    """The voice model in its own process (keeps torch out of the service)."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        env = dict(os.environ)
        token = config.get_secret("hf_token")
        if token:
            env["AKU_HF_TOKEN"] = token
        env.setdefault("OMP_NUM_THREADS", "4")  # leave the rest of the CPU to transcription
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "aku.diarize.worker", "--serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env, preexec_fn=config.die_with_parent,
        )
        ready = self._read()
        if not ready.get("ready"):
            raise RuntimeError(ready.get("error") or "voice model did not load")

    def _read(self) -> dict:
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("voice model process stopped")
        return json.loads(line)

    def embed(self, wav_path: Path, windows: list[tuple[int, int]]) -> np.ndarray:
        with self._lock:
            if self._proc is None:
                self.start()
            self._proc.stdin.write(json.dumps({"wav": str(wav_path), "windows": windows}) + "\n")
            self._proc.stdin.flush()
            reply = self._read()
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return np.frombuffer(base64.b64decode(reply["vectors"]), dtype="<f4").reshape(-1, reply["dim"])

    def close(self) -> None:
        p, self._proc = self._proc, None
        if p and p.poll() is None:
            p.kill()


class SpeakerTracker:
    """Clusters everything heard so far and keeps speaker numbers stable between updates."""

    def __init__(self) -> None:
        self.spans: list[tuple[int, int]] = []
        self.windows: list[cluster.Window] = []
        self.vectors = np.zeros((0, 0), dtype=np.float32)
        self.assigned = np.zeros(0, dtype=int)  # stable speaker number per window (-1 = unusable)
        self.next_number = 1

    def add(self, spans: list[tuple[int, int]], windows: list[cluster.Window], vectors: np.ndarray) -> None:
        self.spans += spans
        self.windows += windows
        self.vectors = vectors if self.vectors.size == 0 else np.vstack([self.vectors, vectors])

    def update(self, speakers: int | None) -> list[dict]:
        """Re-cluster and return turns with speakers "S1", "S2", ..."""
        if not self.windows:
            return []
        labels, _ = cluster.label_windows(self.vectors, self.windows, speakers)
        known = len(self.assigned)
        number: dict[int, int] = {}
        taken: set[int] = set()
        # a cluster keeps the number most of its windows had before
        overlap = Counter((int(c), int(s)) for c, s in zip(labels[:known], self.assigned) if c >= 0 and s >= 0)
        for (c, s), _ in overlap.most_common():
            if c not in number and s not in taken:
                number[c] = s
                taken.add(s)
        for c in labels:  # new voices get the next number, in order of appearance
            c = int(c)
            if c >= 0 and c not in number:
                number[c] = self.next_number
                self.next_number += 1
        self.assigned = np.array([number[int(c)] if c >= 0 else -1 for c in labels], dtype=int)
        turns = cluster.turns_from(self.spans, self.windows, self.assigned)
        for t in turns:
            t["speaker"] = f"S{t['speaker']}" if t["speaker"] > 0 else "S1"
        return turns

    @property
    def found(self) -> int:
        return len({int(s) for s in self.assigned if s >= 0})


class LiveDiarizer:
    def __init__(
        self,
        wav_path: Path,
        flush: Callable[[], None],
        on_turns: Callable[[list[dict], int], None],
        on_error: Callable[[str], None],
        speakers: int | None = None,
        worker: EmbedWorker | None = None,
    ):
        self.wav_path = Path(wav_path)
        self.flush = flush
        self.on_turns = on_turns
        self.on_error = on_error
        self.speakers = speakers
        self.worker = worker or EmbedWorker()
        self.tracker = SpeakerTracker()
        self._vad: SileroVAD | None = None
        self._done_until = 0  # samples already analysed
        self._queue: queue.Queue = queue.Queue()
        self._closed = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="aku-live-diarizer")
        self._thread.start()

    def add(self, start_s: float, end_s: float) -> None:
        """A stretch of the track that was just transcribed."""
        self._queue.put(("add", int(start_s * SR), int(end_s * SR)))

    def set_speakers(self, speakers: int | None) -> None:
        self._queue.put(("speakers", speakers, None))

    def close(self) -> None:
        self._closed = True
        self._queue.put(None)
        self.worker.close()

    def _analyse(self, start: int, end: int) -> None:
        start = max(start, self._done_until)  # chunks overlap a little: never analyse audio twice
        if end - start < int(0.3 * SR):
            return
        self.flush()
        pcm = wav.read(self.wav_path, start, end - start)
        if self._vad is None:
            self._vad = SileroVAD(config.ensure_vad_model())
        spans = cluster.find_phrases(pcm, self._vad, offset=start)
        self._done_until = max(self._done_until, start + len(pcm))
        if not spans:
            return
        windows = cluster.make_windows(spans, first_phrase=len(self.tracker.spans))
        vectors = self.worker.embed(self.wav_path, [(a, b) for _, a, b in windows])
        self.tracker.add(spans, windows, vectors)

    def _run(self) -> None:
        try:
            self.worker.start()
        except Exception as e:  # noqa: BLE001
            if not self._closed:
                self.on_error(f"Live speaker separation is off for this call: {e}")
            return
        while True:
            item = self._queue.get()
            if item is None:
                return
            batch = [item]
            while True:  # several chunks waiting: analyse them all, cluster once
                try:
                    more = self._queue.get_nowait()
                except queue.Empty:
                    break
                if more is None:
                    return
                batch.append(more)
            try:
                for kind, a, b in batch:
                    if kind == "add":
                        self._analyse(a, b)
                    else:
                        self.speakers = a
                turns = self.tracker.update(self.speakers)
                if not self._closed:
                    self.on_turns(turns, self.tracker.found)
            except Exception as e:  # noqa: BLE001
                if not self._closed:
                    self.on_error(f"Live speaker separation stopped: {e}")
                return
