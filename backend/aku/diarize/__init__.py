"""Speaker diarization: runs the worker process and reports progress."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .. import config


class DiarizationError(RuntimeError):
    pass


def choose_engine(setting: str) -> str:
    """auto: pyannote when a Hugging Face token is configured, else the built-in fallback."""
    if setting in ("pyannote", "basic"):
        return setting
    return "pyannote" if config.get_secret("hf_token") else "basic"


def run(
    wav_path: Path,
    out_path: Path,
    speakers: int | None = None,
    engine: str = "auto",
    progress: Callable[[float, str], None] | None = None,
) -> dict:
    engine = choose_engine(engine)
    env = dict(os.environ)
    token = config.get_secret("hf_token")
    if token:
        env["AKU_HF_TOKEN"] = token
    env.setdefault("OMP_NUM_THREADS", str(max(2, (os.cpu_count() or 4) // 2)))
    cmd = [
        sys.executable, "-m", "aku.diarize.worker",
        "--wav", str(wav_path), "--out", str(out_path),
        "--engine", engine, "--speakers", str(speakers) if speakers else "auto",
    ]
    log = Path(out_path).with_name("diarize.log")
    with open(log, "w") as errors:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=errors, text=True, env=env, preexec_fn=config.die_with_parent
        )
    error = None
    for line in proc.stdout:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "error" in msg:
            error = msg["error"]
        elif progress and "progress" in msg:
            progress(float(msg["progress"]), msg.get("step", ""))
    if proc.wait() != 0 or error:
        raise DiarizationError(error or log.read_text(errors="replace").strip()[-400:] or "diarization failed")
    log.unlink(missing_ok=True)
    return json.loads(Path(out_path).read_text())
