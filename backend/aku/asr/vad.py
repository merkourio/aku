"""Silero VAD (ONNX) on 32 ms frames, streaming."""

from __future__ import annotations

from pathlib import Path

import numpy as np

FRAME = 512  # samples at 16 kHz
_CONTEXT = 64


class SileroVAD:
    def __init__(self, model_path: Path):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        self._session = ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])
        self._sr = np.array(16000, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(_CONTEXT, dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        """Speech probability for one frame of FRAME int16 samples."""
        x = frame.astype(np.float32) / 32768.0
        inp = np.concatenate([self._context, x])[None, :]
        out, self._state = self._session.run(None, {"input": inp, "state": self._state, "sr": self._sr})
        self._context = x[-_CONTEXT:]
        return float(out[0, 0])
