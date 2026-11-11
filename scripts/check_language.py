"""Check the language choice: a fixed language, and automatic detection, on Danish and English speech.

Offline (no audio devices). Needs ../scripts/make_test_audio.py to have been run once.
Run:  cd backend && uv run python ../scripts/check_language.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from aku import config
from aku.asr.live import LanguagePicker, transcribe_file
from aku.asr.whisper_cpp import WhisperCpp
from aku.sessions.manager import prompt_maker

ROOT = Path.home() / ".cache" / "aku-test"
results: list[bool] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    results.append(bool(ok))
    print(f"{'OK  ' if ok else 'FAIL'} {name}{' - ' + extra if extra else ''}", flush=True)


def words(text: str) -> list[str]:
    return re.sub(r"[^a-zæøåé' ]", " ", text.lower()).split()


def recall(ref: list[str], hyp: list[str]) -> float:
    """Share of the reference words that the transcript also has."""
    return 1 - sum((Counter(ref) - Counter(hyp)).values()) / max(1, len(ref))


def run(backend, path: Path, track: str, language) -> list:
    segs = []
    transcribe_file(path, track, backend, segs.append, prompt_maker([]), language, on_error=print)
    return segs


def main() -> int:
    settings = config.load_settings()
    backend = WhisperCpp(settings["local_model"], settings["whisper_port"] + 12)
    backend.start()
    danish_ref = words((ROOT / "danish.txt").read_text())
    english_ref = [w for t in json.loads((ROOT / "call_truth.json").read_text()) if t["speaker"] == "me" for w in words(t["text"])]
    try:
        segs = run(backend, ROOT / "danish.wav", "system", "da")
        r = recall(danish_ref, [w for s in segs for w in words(s.text)])
        check("Danish chosen: Danish speech is transcribed in Danish", r > 0.8, f"{r:.0%} of the reference words")
        print("     e.g.", segs[0].text[:100])

        segs = run(backend, ROOT / "danish.wav", "system", "auto")
        r = recall(danish_ref, [w for s in segs for w in words(s.text)])
        langs = Counter(s.language for s in segs)
        check("Automatic: Danish speech is detected as Danish", langs.get("da", 0) >= 0.9 * len(segs), str(dict(langs)))
        check("Automatic: Danish transcript is as good as with Danish chosen", r > 0.8, f"{r:.0%} of the reference words")

        # a mixed call: the other side speaks Danish, I speak English
        picker = LanguagePicker("auto")
        them = run(backend, ROOT / "danish.wav", "system", picker)
        me = run(backend, ROOT / "call_mic.wav", "mic", picker)
        r = recall(english_ref, [w for s in me for w in words(s.text)])
        mine = Counter(s.language for s in me)
        check("Automatic: each track keeps its own language in a mixed call",
              mine.get("en", 0) >= 0.9 * len(me) and all(s.language == "da" for s in them[:3]),
              f"mic {dict(mine)}, {r:.0%} of the English reference words")

        wrong = run(backend, ROOT / "danish.wav", "system", "en")
        r = recall(danish_ref, [w for s in wrong for w in words(s.text)])
        print(f"     for comparison, Danish speech with English chosen keeps only {r:.0%} of the words")
    finally:
        backend.close()
    ok = all(results)
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
