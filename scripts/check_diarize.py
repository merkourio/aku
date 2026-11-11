"""Check speaker separation against the known speakers of the test call, at several lengths.

Run:  cd backend && uv run python ../scripts/check_diarize.py [--engine basic|pyannote]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from aku import diarize
from aku.audio import wav

ROOT = Path.home() / ".cache" / "aku-test"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="auto")
    args = ap.parse_args()
    truth = [t for t in json.loads((ROOT / "call_truth.json").read_text()) if t["speaker"] != "me"]
    audio = wav.read(ROOT / "call_system.wav")
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        for seconds in (45, 120, 275):
            for speakers in (None, 3):
                path = Path(tmp) / "a.wav"
                path.write_bytes(wav.encode(audio[: seconds * 16000]))
                d = diarize.run(path, Path(tmp) / "d.json", speakers, args.engine)
                table: dict[tuple[str, str], float] = {}
                for tr in truth:
                    for t in d["exclusive"]:
                        o = min(tr["end"], t["end"], seconds) - max(tr["start"], t["start"])
                        if o > 0:
                            key = (t["speaker"], tr["speaker"])
                            table[key] = table.get(key, 0) + o
                found = sorted({k[0] for k in table})
                real = len({tr["speaker"] for tr in truth if tr["start"] < seconds - 1})
                total = sum(table.values())
                pure = sum(max(v for (f, _), v in table.items() if f == lab) for lab in found)
                good = len(found) == real and pure / total > 0.97
                ok &= good
                print(f"{d['engine']:8s} {seconds:4d}s speakers={'auto' if speakers is None else speakers}: found {len(found)} of {real},"
                      f" {pure/total:.1%} of speech with the right speaker  {'OK' if good else 'FAIL'}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
