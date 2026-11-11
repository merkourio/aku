"""Check what happens when the recording can no longer be written, on a real recording.

A file-size limit on this process makes writes fail after about 20 seconds of audio, the
same way a full disk would. Expected: Aku ends the recording by itself, says why, and
still turns what it has into a transcript. Uses silent virtual devices.

Run:  cd backend && uv run python ../scripts/check_failures.py
"""

from __future__ import annotations

import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path.home() / ".cache" / "aku-test"
OUT, MIC = "aku_check_out", "aku_check_mic"
LIMIT = 650_000  # bytes per file: about 20 s of one track
results: list[bool] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    results.append(bool(ok))
    print(f"{'OK  ' if ok else 'FAIL'} {name}{' - ' + extra if extra else ''}", flush=True)


def null_sink(name: str) -> str:
    return subprocess.run(
        ["pactl", "load-module", "module-null-sink", f"sink_name={name}", f"sink_properties=device.description={name}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="aku-check-"))
    os.environ.update(AKU_SESSIONS_DIR=str(tmp / "sessions"), AKU_DB=str(tmp / "aku.db"), AKU_CONFIG_DIR=str(tmp / "config"))
    from aku import config, log
    from aku.sessions.manager import Manager

    log.setup()
    config.save_settings({"notifications": False})
    mods = [null_sink(OUT), null_sink(MIC)]
    players = []
    try:
        manager = Manager()
        errors = []
        manager.bus.subscribe(lambda e: e["type"] == "error" and errors.append(e["message"]))
        players = [
            subprocess.Popen(["pw-play", "--target", OUT, str(ROOT / "call_system.wav")], stderr=subprocess.DEVNULL),
            subprocess.Popen(["pw-play", "--target", MIC, str(ROOT / "call_mic.wav")], stderr=subprocess.DEVNULL),
        ]
        _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        resource.setrlimit(resource.RLIMIT_FSIZE, (LIMIT, hard))  # from here on, big files cannot grow: "disk full"
        started = time.monotonic()
        sid = manager.start({"input": MIC, "output": OUT, "scope": "all", "apps": []})["id"]
        while manager.status()["recording"] and time.monotonic() - started < 60:
            time.sleep(0.5)
        took = time.monotonic() - started
        check("recording ends by itself when the audio cannot be written", not manager.status()["recording"], f"after {took:.0f}s")
        for _ in range(240):
            d = manager.detail(sid)
            if d["status"] not in ("recording", "processing"):
                break
            time.sleep(0.5)
        check("the user is told why", any("stopped early" in e for e in errors), errors[0] if errors else "no message")
        check("what was recorded becomes a transcript", d["status"] == "naming" and len(d["lines"]) >= 2,
              f"status {d['status']}, {len(d['lines'])} lines, last ends at {max((l['end'] for l in d['lines']), default=0):.0f}s")
        check("the reason stays on the session", "stopped early" in (d["error"] or ""), d["error"] or "")
        size = (tmp / "sessions" / sid / "mic.wav").stat().st_size
        check("the audio file is intact up to the failure", 0.8 * LIMIT < size <= LIMIT, f"{size} bytes")
    finally:
        for p in players:
            p.terminate()
        for m in mods:
            subprocess.run(["pactl", "unload-module", m])
    ok = all(results)
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
