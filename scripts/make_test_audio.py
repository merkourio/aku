"""Build test recordings from Mini LibriSpeech (public domain audiobooks, CC BY 4.0 corpus).

Creates in ~/.cache/aku-test:
  bench10.wav          10 minutes of continuous speech (benchmark), bench10.txt its text
  call_system.wav      a 3-speaker "call" (the other side)
  call_mic.wav         a 4th speaker on the same timeline (me)
  call_truth.json      who said what, when

Run:  cd backend && uv run python ../scripts/make_test_audio.py
"""

from __future__ import annotations

import json
import random
import tarfile
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path.home() / ".cache" / "aku-test"
URL = "https://www.openslr.org/resources/31/dev-clean-2.tar.gz"
SR = 16000


def corpus() -> Path:
    base = ROOT / "LibriSpeech" / "dev-clean-2"
    if not base.exists():
        tar = ROOT / "dev-clean-2.tar.gz"
        if not tar.exists():
            ROOT.mkdir(parents=True, exist_ok=True)
            print("downloading", URL)
            urllib.request.urlretrieve(URL, tar)
        with tarfile.open(tar) as t:
            t.extractall(ROOT, filter="data")
    return base


def utterances(base: Path) -> dict[str, list[tuple[Path, str]]]:
    out: dict[str, list[tuple[Path, str]]] = {}
    for trans in sorted(base.glob("*/*/*.trans.txt")):
        for line in trans.read_text().splitlines():
            uid, text = line.split(" ", 1)
            out.setdefault(uid.split("-")[0], []).append((trans.parent / f"{uid}.flac", text))
    return out


def load(path: Path) -> np.ndarray:
    audio, sr = sf.read(path, dtype="int16")
    assert sr == SR
    return audio


def main() -> None:
    rng = random.Random(7)
    utts = utterances(corpus())
    speakers = sorted(utts, key=lambda s: -len(utts[s]))

    # benchmark: 10 minutes, speakers back to back
    parts, texts, total = [], [], 0
    for spk in speakers:
        for path, text in utts[spk][:12]:
            a = load(path)
            if total + len(a) > 600 * SR:
                break  # whole utterances only, so the reference text matches the audio
            parts.append(a)
            texts.append(text)
            total += len(a)
        if total >= 590 * SR:
            break
    parts.append(np.zeros(600 * SR - total, dtype=np.int16))
    sf.write(ROOT / "bench10.wav", np.concatenate(parts), SR)
    (ROOT / "bench10.txt").write_text("\n".join(texts))

    # a call: A, B, C on the system track, D on the mic track
    them = speakers[20:23]
    me = speakers[23]
    pools = {s: [u for u in utts[s] if 2.0 < sf.info(u[0]).duration < 12.0] for s in them + [me]}
    order = []
    for i in range(34):
        who = me if i % 4 == 3 else rng.choice([s for s in them if not order or s != order[-1]])
        order.append(who)
    system = np.zeros(0, dtype=np.int16)
    mic = np.zeros(0, dtype=np.int16)
    truth = []
    t = int(1.0 * SR)
    for i, who in enumerate(order):
        path, text = pools[who].pop(0)
        a = load(path)
        end = t + len(a)
        if who == me:
            mic = np.concatenate([mic, np.zeros(max(0, t - len(mic)), dtype=np.int16), a])
        else:
            system = np.concatenate([system, np.zeros(max(0, t - len(system)), dtype=np.int16), a])
        truth.append({"speaker": "me" if who == me else f"spk{them.index(who)}", "start": t / SR, "end": end / SR, "text": text})
        # mostly normal pauses, sometimes a quick hand-over inside one chunk
        t = end + int(rng.choice([0.15, 0.2, 0.5, 0.8, 1.2]) * SR)
    n = max(len(system), len(mic)) + SR
    system = np.concatenate([system, np.zeros(n - len(system), dtype=np.int16)])
    mic = np.concatenate([mic, np.zeros(n - len(mic), dtype=np.int16)])
    sf.write(ROOT / "call_system.wav", system, SR)
    sf.write(ROOT / "call_mic.wav", mic, SR)
    (ROOT / "call_truth.json").write_text(json.dumps(truth, indent=1))
    print(f"bench10.wav 600s; call {n/SR:.0f}s with {len(truth)} turns, speakers {them} + me {me}")


if __name__ == "__main__":
    main()
