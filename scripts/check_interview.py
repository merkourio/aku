"""M9 check: interview mode on a simulated in-person interview.

Three people (the owner and two guests) talk into one microphone. The owner's voice is
enrolled first from separate sentences, then the interview is recorded with the microphone
only, transcribed live and separated into speakers. Everything runs on a silent virtual
device: nothing is played on real speakers and no real microphone is used.

Run:  cd backend && uv run python ../scripts/check_interview.py [--seconds 600] [--mic-speakers auto]
Needs ../scripts/make_test_audio.py to have been run once (for the audiobook corpus).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from check_live import null_sink, speaker_table
from make_test_audio import ROOT, SR, corpus, load, utterances

MIC = "aku_check_room"


def build_room(seconds: float) -> tuple[Path, Path, list[dict]]:
    """interview_room.wav (owner + two guests taking turns), interview_me.wav (the owner alone), and who spoke when."""
    room, me_clip, truth_path = ROOT / "interview_room.wav", ROOT / "interview_me.wav", ROOT / "interview_truth.json"
    if room.exists() and me_clip.exists() and truth_path.exists() and sf.info(room).duration >= seconds - 1:
        return room, me_clip, json.loads(truth_path.read_text())
    rng = random.Random(11)
    utts = utterances(corpus())
    by_length = sorted(utts, key=lambda s: -sum(sf.info(p).duration for p, _ in utts[s]))
    people = {"me": by_length[0], "guest_a": by_length[1], "guest_b": by_length[2]}  # the three with the most audio
    pools = {who: list(utts[spk]) for who, spk in people.items()}

    # the owner's enrolment: sentences that do not come back in the interview
    parts, total = [], 0
    while total < 34 * SR:
        a = load(pools["me"].pop(0)[0])
        parts += [a, np.zeros(int(0.4 * SR), dtype=np.int16)]
        total += len(a)
    sf.write(me_clip, np.concatenate(parts), SR)

    audio = [np.zeros(SR, dtype=np.int16)]
    truth, t, last = [], SR, None
    while t < seconds * SR:
        who = rng.choice([w for w in pools if w != last and pools[w]] or [w for w in pools if pools[w]] or [None])
        if who is None:
            break
        path, text = pools[who].pop(0)
        a = load(path)
        truth.append({"speaker": who, "start": t / SR, "end": (t + len(a)) / SR, "text": text})
        gap = np.zeros(int(rng.choice([0.2, 0.5, 0.8, 1.2]) * SR), dtype=np.int16)
        audio += [a, gap]
        t += len(a) + len(gap)
        last = who
    sf.write(room, np.concatenate(audio), SR)
    truth_path.write_text(json.dumps(truth, indent=1))
    return room, me_clip, truth


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=600)
    ap.add_argument("--mic-speakers", default="auto", help='"auto" or the number of people at the microphone')
    ap.add_argument("--backend", default="local")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    room, me_clip, truth = build_room(args.seconds)
    seconds = min(args.seconds, sf.info(room).duration)
    truth = [t for t in truth if t["end"] < seconds - 2]
    print(f"interview: {seconds:.0f}s, {len(truth)} turns, speech per person: "
          + ", ".join(f"{w} {sum(t['end'] - t['start'] for t in truth if t['speaker'] == w):.0f}s" for w in ("me", "guest_a", "guest_b")))

    tmp = Path(tempfile.mkdtemp(prefix="aku-check-"))
    os.environ.update(AKU_SESSIONS_DIR=str(tmp / "sessions"), AKU_DB=str(tmp / "aku.db"), AKU_CONFIG_DIR=str(tmp / "config"))
    from aku import config
    from aku.diarize import voiceprints
    from aku.sessions.manager import Manager

    config.save_settings({"notifications": False, "user_name": "Taylor", "output_folder": str(tmp / "out")})
    module = null_sink(MIC)
    player = None
    results: list[bool] = []

    def check(name: str, good: bool, extra: str = "") -> None:
        results.append(bool(good))
        print(f"{'OK  ' if good else 'FAIL'} {name}{' - ' + extra if extra else ''}", flush=True)

    try:
        manager = Manager()
        manager.bus.subscribe(lambda e: e["type"] == "error" and print("  ERROR:", e["message"], flush=True))

        # 1. enrol the owner's voice
        player = subprocess.Popen(["pw-play", "--target", MIC, str(me_clip)], stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        info = manager.enrol_me(30, input=MIC)
        player.terminate()
        check("my voice is enrolled", bool(info), ", ".join(i["space"] for i in info))

        # 2. the interview: microphone only
        player = subprocess.Popen(["pw-play", "--target", MIC, str(room)], stderr=subprocess.DEVNULL)
        time.sleep(0.3)
        started = time.monotonic()
        sid = manager.start({"input": MIC, "output": None, "scope": "none", "apps": []}, backend=args.backend, interview=True)["id"]
        time.sleep(min(120, seconds / 2))
        now = time.monotonic() - started
        status = manager.status()
        live = [l for l in manager.detail(sid)["lines"] if l["end"] + 0.3 < now - 8]
        table, pure, total = speaker_table(live, [t for t in truth if t["end"] < now - 8], 0.3)
        mine = {t: v for (label, t), v in table.items() if label == "me"}
        check(f"live at {now:.0f}s: 3 people shown at the microphone", status["live"]["mic"]["found"] == 3, f"shown {status['live']['mic']['found']}")
        check("live: words with the right speaker", total > 0 and pure / max(1, total) > 0.9, f"{pure}/{total}")
        check("live: my lines carry my name", bool(mine) and max(mine, key=mine.get) == "me" and mine["me"] / sum(mine.values()) > 0.9, str(mine))
        check("live: no computer audio recorded", status["system_off"] and not any(l["speaker"].startswith(("S", "them")) for l in live))
        time.sleep(max(0, seconds - (time.monotonic() - started)))
        manager.stop(sid, mic_speakers=args.mic_speakers)
        stopped = time.monotonic()
        manager.wait(sid)
        d = manager.detail(sid)
        meta = manager.session(sid).meta
        lag = meta.get("lag") or {}
        print(f"status {d['status']}; recorded {d['duration']:.0f}s; processing after stop took {time.monotonic() - stopped:.0f}s"
              f" (speaker separation {meta.get('diarization_seconds')}s, engine {d['diarizer']}); lag median {lag.get('median')}s, max {lag.get('max')}s")

        # 3. the result
        people = d["speakers"]
        print("speakers:", [f"{p['id']} {p['display']} {p['duration']}s{' (me)' if p['me'] else ''}" for p in people])
        check("3 of 3 people found at the microphone", len(people) == 3 and all(p["track"] == "mic" for p in people), f"found {len(people)}")
        check("exactly one of them is me, found by voice", [p["me"] for p in people].count(True) == 1)
        table, pure, total = speaker_table(d["lines"], truth, 0.3)
        for label in sorted({k[0] for k in table}):
            print("  ", label, {t: v for (j, t), v in sorted(table.items()) if j == label})
        check("words with the right speaker", total > 0 and pure / total > 0.95, f"{pure}/{total} = {pure / max(1, total):.1%}")
        mine = {t: v for (label, t), v in table.items() if label == "me"}
        check("the speaker labelled as me is really me", bool(mine) and max(mine, key=mine.get) == "me", str(mine))
        ref_words = sum(len(t["text"].split()) for t in truth)
        hyp_words = sum(len(l["text"].split()) for l in d["lines"])
        check("no words lost or doubled", abs(hyp_words - ref_words) / ref_words < 0.05, f"transcript {hyp_words}, reference {ref_words}")

        # 4. name the guests from their clips, finalise
        guests = [p for p in people if not p["me"]]
        for n, p in enumerate(guests):
            assert len(manager.speaker_clip(sid, p["id"])) > 16000
            manager.set_speaker(sid, p["id"], ["Anna", "Bo", "Cy"][n % 3], remember=False)
        final = manager.finalize(sid, title="Check interview")
        text = Path(final["output_path"]).read_text()
        check("Markdown has me and the named guests", all(f"{n}:**" in text for n in ["Taylor", "Anna", "Bo"][: 1 + len(guests)]))
        print("\n".join(text.splitlines()[:14]))
        check("the enrolled voice is unchanged by a matched interview", len(voiceprints.me_info()) == len(info))
        check("interview mode is not remembered", config.load_settings()["last_sources"]["scope"] != "none")
        if args.keep:
            print("session folder:", manager.session(sid).dir)
    finally:
        if player:
            player.terminate()
        subprocess.run(["pactl", "unload-module", module])
    ok = bool(results) and all(results)
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
