"""M2 + M3 check: the whole flow on a simulated call.

Plays the 3-speaker test call (other side) and a 4th speaker (mic) through
silent virtual devices, records and transcribes live, then separates speakers.
Nothing is played on real speakers and no real microphone is used.

Run:  cd backend && uv run python ../scripts/check_live.py [--seconds 275] [--scope apps] [--backend local]
Needs ../scripts/make_test_audio.py to have been run once.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path.home() / ".cache" / "aku-test"
OUT, MIC = "aku_check_out", "aku_check_mic"


def null_sink(name: str) -> str:
    return subprocess.run(
        ["pactl", "load-module", "module-null-sink", f"sink_name={name}", f"sink_properties=device.description={name}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def long_run(args) -> int:
    """M2 acceptance: 10 minutes, lag under target, no words lost or doubled at chunk edges."""
    import re
    from collections import Counter

    tmp = Path(tempfile.mkdtemp(prefix="aku-check-"))
    os.environ.update(AKU_SESSIONS_DIR=str(tmp / "sessions"), AKU_DB=str(tmp / "aku.db"), AKU_CONFIG_DIR=str(tmp / "config"))
    from aku import config
    from aku.sessions.manager import Manager

    config.save_settings({"notifications": False})
    mods = [null_sink(OUT), null_sink(MIC)]
    player = None
    try:
        manager = Manager()
        manager.bus.subscribe(lambda e: e["type"] == "error" and print("  ERROR:", e["message"], flush=True))
        player = subprocess.Popen(["pw-play", "--target", OUT, str(ROOT / "bench10.wav")], stderr=subprocess.DEVNULL)
        sid = manager.start({"input": MIC, "output": OUT, "scope": "all", "apps": []}, backend=args.backend)["id"]
        time.sleep(603)
        manager.stop(sid, speakers="auto")
        manager.wait(sid)
        session = manager.session(sid)
        meta, segs = session.meta, session.segments()
        lag = meta.get("lag") or {}
        lag_ok = bool(lag) and lag["median"] < 10 and lag["max"] < 25
        norm = lambda t: re.sub(r"[^a-z' ]", " ", t.lower()).split()
        ref = norm((ROOT / "bench10.txt").read_text())
        hyp = [w for s in sorted(segs, key=lambda s: s.start) for w in norm(s.text)]
        longest = max(s.chunk_end - s.chunk_start for s in segs)
        # words the transcript has too many or too few of, compared with the reference text
        extra = sum((Counter(hyp) - Counter(ref)).values())
        lost = sum((Counter(ref) - Counter(hyp)).values())
        words_ok = abs(len(hyp) - len(ref)) / len(ref) < 0.02 and lost / len(ref) < 0.06
        print(f"recorded {meta['duration']:.0f}s, {len(segs)} segments, longest chunk {longest:.1f}s")
        print(f"lag: median {lag.get('median')}s, max {lag.get('max')}s  {'OK' if lag_ok else 'FAIL'} (target <10 / <25)")
        print(f"words: transcript {len(hyp)}, reference {len(ref)}; {lost} reference words missing or heard differently,"
              f" {extra} extra  {'OK' if words_ok else 'FAIL'}")
        ok = lag_ok and words_ok and longest <= 12.6
    finally:
        if player:
            player.terminate()
        for m in mods:
            subprocess.run(["pactl", "unload-module", m])
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=275)
    ap.add_argument("--scope", choices=["all", "apps"], default="all")
    ap.add_argument("--backend", default="local")
    ap.add_argument("--speakers", default="3")
    ap.add_argument("--keep", action="store_true", help="keep the session folder and print its path")
    ap.add_argument("--long", action="store_true", help="10 minutes of back-to-back speech: lag and chunk-edge check only")
    args = ap.parse_args()
    if args.long:
        return long_run(args)

    tmp = Path(tempfile.mkdtemp(prefix="aku-check-"))
    os.environ["AKU_SESSIONS_DIR"] = str(tmp / "sessions")
    os.environ["AKU_DB"] = str(tmp / "aku.db")
    os.environ["AKU_CONFIG_DIR"] = str(tmp / "config")

    from aku import config
    from aku.sessions.manager import Manager

    config.save_settings({"notifications": False, "user_name": "Taylor", "output_folder": str(tmp / "out")})
    mods = [null_sink(OUT), null_sink(MIC)]
    players = []
    ok = True
    try:
        manager = Manager()
        manager.bus.subscribe(lambda e: e["type"] == "error" and print("  ERROR:", e["message"]))
        manager.bus.subscribe(
            lambda e: e["type"] == "segment"
            and print(f"  [{e['segment']['start']:6.1f}] {e['segment']['name']:6s} lag {e['segment']['lag']:4.1f}s  {e['segment']['text'][:80]}")
        )
        players = [
            subprocess.Popen(["pw-play", "--target", OUT, str(ROOT / "call_system.wav")], stderr=subprocess.DEVNULL),
            subprocess.Popen(["pw-play", "--target", MIC, str(ROOT / "call_mic.wav")], stderr=subprocess.DEVNULL),
        ]
        time.sleep(0.3)
        sources = {"input": MIC, "output": OUT, "scope": args.scope, "apps": ["pw-play"]}
        if args.scope == "apps":
            # only the player on the "call" device counts as the app; the mic player is on another device
            print("note: app scope captures every pw-play stream, including the simulated mic")
        started = time.monotonic()
        s = manager.start(sources, backend=args.backend)
        sid = s["id"]
        time.sleep(args.seconds)
        manager.stop(sid, speakers=args.speakers)
        stopped = time.monotonic()
        manager.wait(sid)
        d = manager.detail(sid)
        meta = manager.session(sid).meta
        print(f"\nstatus {d['status']}; recorded {d['duration']:.1f}s; processing after stop took {time.monotonic()-stopped:.1f}s"
              f" (diarization {meta.get('diarization_seconds')}s, engine {d['diarizer']})")

        lag = meta.get("lag") or {}
        lag_ok = bool(lag) and lag["median"] < 10 and lag["max"] < 25
        ok &= lag_ok
        print(f"lag: median {lag.get('median')}s, max {lag.get('max')}s over {lag.get('count')} segments  {'OK' if lag_ok else 'FAIL'} (target <10 / <25)")

        truth = json.loads((ROOT / "call_truth.json").read_text())
        truth = [t for t in truth if t["start"] < args.seconds - 2]
        offset = 0.3  # players started this long before the recording
        table: dict[tuple[str, str], float] = {}
        for line in d["lines"]:
            best, best_o = None, 0.0
            for t in truth:
                o = min(line["end"] + offset, t["end"]) - max(line["start"] + offset, t["start"])
                if o > best_o:
                    best, best_o = t["speaker"], o
            if best:
                key = (line["speaker"], best)
                table[key] = table.get(key, 0) + len(line["text"].split())
        ids = sorted({k[0] for k in table})
        total = sum(table.values())
        pure = sum(max(v for (i, _), v in table.items() if i == sid_) for sid_ in ids)
        print("speakers found:", [f"{s['id']} ({s['duration']}s)" for s in d["speakers"]])
        for i in ids:
            print("  ", i, {t: v for (j, t), v in sorted(table.items()) if j == i})
        want = len({t["speaker"] for t in truth if t["speaker"] != "me"})
        spk_ok = len(d["speakers"]) == want and pure / total > 0.97
        ok &= spk_ok
        print(f"words with the right speaker: {pure}/{total} = {pure/total:.1%}; speakers {len(d['speakers'])} (expected {want})  {'OK' if spk_ok else 'FAIL'}")

        ref_words = sum(len(t["text"].split()) for t in truth)
        hyp_words = sum(len(l["text"].split()) for l in d["lines"])
        words_ok = abs(hyp_words - ref_words) / ref_words < 0.05
        ok &= words_ok
        print(f"words: transcript {hyp_words}, reference {ref_words}  {'OK' if words_ok else 'FAIL'} (cut or doubled words at chunk edges would show here)")

        # name, finalise, check the Markdown lands in the output folder
        for sp in d["speakers"]:
            manager.set_speaker(sid, sp["id"], f"Person {sp['id'][1:]}", remember=False)
        final = manager.finalize(sid, title="Check call")
        path = Path(final["output_path"])
        md_ok = path.exists() and "**[00:" in path.read_text() and not manager.session(sid).temp_md.exists()
        ok &= md_ok
        print(f"finalised to {path}  {'OK' if md_ok else 'FAIL'}")
        print("\n".join(path.read_text().splitlines()[:16]))
        if args.keep:
            print("session folder:", manager.session(sid).dir)
    finally:
        for p in players:
            p.terminate()
        for m in mods:
            subprocess.run(["pactl", "unload-module", m])
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
