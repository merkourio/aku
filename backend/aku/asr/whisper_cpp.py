"""Local backend: a whisper.cpp server process (Vulkan GPU), kept loaded between chunks."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import numpy as np

from .. import config
from ..audio import wav
from ..config import SAMPLE_RATE as SR
from .base import Word, is_non_speech, tidy, tokens_to_words

MIN_SAMPLES = int(1.1 * SR)  # whisper.cpp rejects very short input: pad with silence


class WhisperError(RuntimeError):
    pass


def free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise WhisperError("no free port for whisper-server")


class WhisperCpp:
    word_timestamps = True

    def __init__(self, model: str, port: int = 4588, use_gpu: bool = True, threads: int = 4, on_progress=None):
        self.model = model
        self.name = f"whisper.cpp:{model}"
        self.port = port
        self.use_gpu = use_gpu
        self.threads = threads
        self.on_progress = on_progress
        self._proc: subprocess.Popen | None = None
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()
        self._log: Path | None = None

    # -- process ------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return
            binary = config.whisper_bin("whisper-server")
            if binary is None:
                raise WhisperError("whisper-server not found. Run scripts/build-whisper.sh first.")
            model = config.model_path(self.model)
            if not model.exists():
                config.download(config.MODEL_URL.format(name=self.model), model, self.on_progress)
            self.port = free_port(self.port)
            cmd = [
                str(binary), "-m", str(model), "--host", "127.0.0.1", "--port", str(self.port),
                "-t", str(self.threads), "-sns",
            ]
            if not self.use_gpu:
                cmd.append("-ng")
            self._log = config.data_dir() / f"whisper-server-{self.port}.log"
            self._proc = subprocess.Popen(cmd, stdout=open(self._log, "wb"), stderr=subprocess.STDOUT)
            self._client = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=300)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    raise WhisperError(f"whisper-server exited: {self._tail()}")
                try:
                    with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                        return
                except OSError:
                    time.sleep(0.2)
            raise WhisperError("whisper-server did not start in time")

    def _tail(self) -> str:
        try:
            return self._log.read_text(errors="replace")[-400:]
        except Exception:
            return ""

    def close(self) -> None:
        with self._lock:
            if self._client:
                self._client.close()
                self._client = None
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None

    # -- inference ------------------------------------------------------------

    def transcribe(self, audio: np.ndarray, prompt: str = "", language: str = "en") -> list[Word]:
        self.start()
        duration = len(audio) / SR
        if len(audio) < MIN_SAMPLES:
            audio = np.concatenate([audio, np.zeros(MIN_SAMPLES - len(audio), dtype=np.int16)])
        data = {"response_format": "verbose_json", "temperature": "0.0", "language": language}
        if prompt:
            data["prompt"] = prompt
        try:
            r = self._client.post("/inference", data=data, files={"file": ("chunk.wav", wav.encode(audio), "audio/wav")})
            r.raise_for_status()
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise WhisperError(f"whisper-server request failed: {e}") from e
        if "error" in body:
            raise WhisperError(str(body["error"]))
        # The server splits segments by length, sometimes mid-word: merge tokens across segments.
        tokens = []
        for seg in body.get("segments") or []:
            if is_non_speech(seg.get("text", "")):
                continue
            tokens += [(t.get("start", seg.get("start", 0.0)), t.get("end", seg.get("end", 0.0)), t.get("word", ""))
                       for t in seg.get("words") or []]
        return tidy(tokens_to_words(tokens), duration)
