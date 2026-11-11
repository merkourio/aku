"""M1 check: are both tracks aligned to the wall clock, also when an app's stream drops out?

Uses a silent virtual output device, so nothing is played on real speakers.
Run:  cd backend && uv run python ../scripts/check_capture.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from aku.audio import pipewire, wav
from aku.audio.capture import SR, Capture

SINK = "aku_check"


def click_track(path: Path, seconds: float) -> None:
    """A click every 0.5 s with a different pitch each time, so offsets are unambiguous."""
    sr = 48000
    out = np.zeros(int(seconds * sr), dtype=np.float32)
    rng = np.random.default_rng(1)
    for i in range(int(seconds * 2)):
        n = int(0.05 * sr)
        burst = rng.standard_normal(n).astype(np.float32) * np.hanning(n)
        out[i * sr // 2 : i * sr // 2 + n] = burst * 0.5
    pcm = (np.stack([out, out], axis=1) * 32767).astype("<i2")
    import soundfile as sf

    sf.write(path, pcm, sr)


def offset_ms(a: np.ndarray, b: np.ndarray, max_ms: int = 1000) -> float:
    """How much later b is than a (positive = b delayed), by cross-correlation."""
    n = min(len(a), len(b))
    size = 1 << (2 * n - 1).bit_length()
    fa = np.fft.rfft(a[:n].astype(np.float64), size)
    fb = np.fft.rfft(b[:n].astype(np.float64), size)
    corr = np.fft.irfft(fb * np.conj(fa), size)
    lim = max_ms * SR // 1000
    lags = np.concatenate([np.arange(0, lim + 1), np.arange(-lim, 0)])
    vals = np.concatenate([corr[: lim + 1], corr[-lim:]])
    return float(lags[int(np.argmax(vals))]) * 1000 / SR


def play(path: Path) -> subprocess.Popen:
    return subprocess.Popen(["pw-play", "--target", SINK, str(path)], stderr=subprocess.DEVNULL)


def main() -> int:
    mod = subprocess.run(
        ["pactl", "load-module", "module-null-sink", f"sink_name={SINK}", "sink_properties=device.description=AkuCheck"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    ok = True
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            clicks = tmp / "clicks.wav"
            click_track(clicks, 30)

            for scope in ("all", "apps"):
                d = tmp / scope
                d.mkdir()
                player = play(clicks)
                time.sleep(1.0)
                app_key = next((n.app_key for n in pipewire.snapshot().app_streams() if n.app_key.startswith("pw-")), "pw-play")
                sources = {"input": SINK, "output": SINK, "scope": scope, "apps": [app_key]}
                cap = Capture(d, sources, tag=f"check{scope}")
                cap.mic_stream.capture_sink = True  # the "mic" listens to the same virtual device
                trace = []
                if os.environ.get("AKU_TRACE") and scope == "apps":
                    orig_feed, orig_tick, tr = cap.system.feed, cap.system.tick, cap.system
                    def feed(pcm, now=None):
                        before = tr.padded
                        d = int((now - tr.t0) * SR) - (tr.written + len(pcm))
                        orig_feed(pcm, now)
                        trace.append((now - tr.t0, "feed", len(pcm), d, tr.padded - before, int(np.abs(pcm).max())))
                    def tick(now=None):
                        before = tr.padded
                        orig_tick(now)
                        trace.append((time.monotonic() - tr.t0, "tick", 0, 0, tr.padded - before, 0))
                    cap.system.feed, cap.system.tick = feed, tick
                cap.start()
                start = time.monotonic()
                if scope == "apps":
                    time.sleep(6)
                    player.terminate()  # the app goes away mid-recording...
                    time.sleep(3)
                    player = play(clicks)  # ...and comes back
                    time.sleep(7)
                else:
                    time.sleep(12)
                wall = cap.stop()
                player.terminate()
                for t, kind, n, dfc, pad, peak in trace:
                    if 5.8 < t < 11.5 or pad:
                        print(f"   {t:7.3f} {kind} n={n:5d} deficit={dfc:6d} pad={pad:5d} peak={peak}")
                mic = wav.read(d / "mic.wav")
                system = wav.read(d / "system.wav")
                print(f"\nscope={scope}  wall-clock {wall:.3f}s  mic {len(mic)/SR:.3f}s  system {len(system)/SR:.3f}s")
                print(f"  padded: mic {cap.mic.padded/SR*1000:.0f} ms, system {cap.system.padded/SR*1000:.0f} ms; errors: {cap.errors() or 'none'}")
                for name, x in (("mic", mic), ("system", system)):
                    diff = abs(len(x) / SR - wall) * 1000
                    good = diff < 100
                    ok &= good
                    print(f"  {name} length vs wall clock: {diff:.1f} ms  {'OK' if good else 'FAIL'}")
                if scope == "all":
                    windows = [(2, 11)]
                else:
                    windows = [(2, 5), (12, 15)]  # before the gap, after the app came back
                for a, b in windows:
                    seg_m, seg_s = mic[a * SR : b * SR], system[a * SR : b * SR]
                    if np.abs(seg_s).max() < 200:
                        print(f"  {a}-{b}s: system track is silent  FAIL")
                        ok = False
                        continue
                    off = offset_ms(seg_m, seg_s)
                    good = abs(off) < 100
                    ok &= good
                    print(f"  {a}-{b}s: system vs mic offset {off:+.1f} ms  {'OK' if good else 'FAIL'}")
                if scope == "apps":
                    gap = system[int(7.5 * SR) : int(8.5 * SR)]
                    good = np.abs(gap).max() < 200
                    ok &= good
                    print(f"  gap while app was closed is silent: {'OK' if good else 'FAIL'}")
    finally:
        subprocess.run(["pactl", "unload-module", mod])
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
