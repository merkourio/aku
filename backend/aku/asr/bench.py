"""M0 benchmark: real-time factor of whisper.cpp models on this machine."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .. import config
from ..audio import wav

MODELS = ["large-v3-turbo", "medium", "small"]


def run_one(model: str, wav_path: Path, use_gpu: bool = True, threads: int = 4) -> dict:
    cli = config.whisper_bin("whisper-cli")
    if cli is None:
        raise RuntimeError("whisper-cli not found. Run scripts/build-whisper.sh first.")
    mp = config.model_path(model)
    if not mp.exists():
        print(f"  downloading {model}...")
        config.download(config.MODEL_URL.format(name=model), mp)
    cmd = [str(cli), "-m", str(mp), "-f", str(wav_path), "-l", "en", "-t", str(threads)]
    if not use_gpu:
        cmd.append("-ng")
    r = subprocess.run(cmd, capture_output=True, text=True)
    log = r.stderr + r.stdout
    def ms(label: str) -> float:
        m = re.search(rf"{label} time\s*=\s*([\d.]+) ms", log)
        return float(m.group(1)) if m else 0.0
    duration = wav.frames(wav_path) / 16000
    work = ms("total") - ms("load")
    return {
        "model": model,
        "gpu": use_gpu and "Vulkan" in log,
        "audio_s": duration,
        "work_s": work / 1000,
        "rtf": work / 1000 / duration if duration else 0.0,
        "ok": r.returncode == 0 and work > 0,
    }


def run(wav_path: Path, models: list[str] | None = None, use_gpu: bool = True) -> list[dict]:
    results = []
    for m in models or MODELS:
        print(f"benchmarking {m} ({'GPU' if use_gpu else 'CPU'})...", flush=True)
        res = run_one(m, wav_path, use_gpu)
        results.append(res)
        if res["ok"]:
            print(f"  {res['audio_s']:.0f}s of audio in {res['work_s']:.1f}s -> {res['rtf']:.2f}x real time"
                  f" ({'GPU' if res['gpu'] else 'CPU'})", flush=True)
        else:
            print("  failed", flush=True)
    return results


def pick(results: list[dict], limit: float = 0.5) -> str | None:
    """The first (best) model that runs within the limit."""
    for r in results:
        if r["ok"] and r["rtf"] <= limit:
            return r["model"]
    return None
