"""Offline check of chunking + local ASR on the test call (no audio devices needed).

Run:  cd backend && uv run python ../scripts/check_transcribe.py
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from aku import config
from aku.asr.live import transcribe_file
from aku.asr.whisper_cpp import WhisperCpp

ROOT = Path.home() / ".cache" / "aku-test"


def norm(text: str) -> list[str]:
    return re.sub(r"[^a-z' ]", " ", text.lower()).split()


def main() -> None:
    settings = config.load_settings()
    backend = WhisperCpp(settings["local_model"], settings["whisper_port"] + 10)
    t = time.monotonic()
    backend.start()
    print(f"server ready in {time.monotonic()-t:.1f}s")
    truth = json.loads((ROOT / "call_truth.json").read_text())
    for track, name in (("system", "call_system.wav"), ("mic", "call_mic.wav")):
        segs = []
        t = time.monotonic()
        transcribe_file(ROOT / name, track, backend, segs.append, on_error=print)
        took = time.monotonic() - t
        ref = [w for turn in truth if (turn["speaker"] == "me") == (track == "mic") for w in norm(turn["text"])]
        hyp = [w for s in segs for w in norm(s.text)]
        missing = len(set(ref) - set(hyp))
        durations = [s.chunk_end - s.chunk_start for s in segs]
        print(f"{track}: {len(segs)} segments in {took:.1f}s; chunks avg {sum(durations)/len(durations):.1f}s max {max(durations):.1f}s;"
              f" words ref {len(ref)} hyp {len(hyp)}; distinct ref words missing {missing}")
        for s in segs[:6]:
            print(f"   [{s.start:6.2f}-{s.end:6.2f}] {s.text[:110]}")
    backend.close()


if __name__ == "__main__":
    main()
