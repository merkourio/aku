"""M7 check: crash recovery, the quality pass and voiceprints, against a real service process.

Starts its own `aku serve` on a spare port with a throwaway data folder, records the
simulated call, kills the service mid-recording, restarts it and recovers. Then names a
speaker with "remember", runs Improve, and checks the voice is suggested on a second call.

Run:  cd backend && uv run python ../scripts/check_recovery.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path.home() / ".cache" / "aku-test"
PORT = 4597
BASE = f"http://127.0.0.1:{PORT}"
OUT, MIC = "aku_check_out", "aku_check_mic"
results: list[bool] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    results.append(bool(ok))
    print(f"{'OK  ' if ok else 'FAIL'} {name}{' - ' + extra if extra else ''}", flush=True)


def null_sink(name: str) -> str:
    return subprocess.run(
        ["pactl", "load-module", "module-null-sink", f"sink_name={name}", f"sink_properties=device.description={name}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def serve(env: dict) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-m", "aku.cli", "serve", "--no-tray"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            httpx.get(BASE + "/status", timeout=1)
            return proc
        except httpx.HTTPError:
            time.sleep(0.2)
    raise RuntimeError("service did not start")


def wait_idle(sid: str, timeout: float = 900) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if sid not in httpx.get(BASE + "/status").json()["jobs"]:
            return httpx.get(f"{BASE}/sessions/{sid}").json()
        time.sleep(0.5)
    raise RuntimeError("job did not finish")


def players() -> list[subprocess.Popen]:
    return [
        subprocess.Popen(["pw-play", "--target", OUT, str(ROOT / "call_system.wav")], stderr=subprocess.DEVNULL),
        subprocess.Popen(["pw-play", "--target", MIC, str(ROOT / "call_mic.wav")], stderr=subprocess.DEVNULL),
    ]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="aku-recovery-"))
    env = dict(os.environ, AKU_SESSIONS_DIR=str(tmp / "sessions"), AKU_DB=str(tmp / "aku.db"), AKU_CONFIG_DIR=str(tmp / "config"))
    (tmp / "config").mkdir()
    (tmp / "config" / "settings.json").write_text(json.dumps({
        "port": PORT, "whisper_port": 4598, "notifications": False, "improve_model": "medium",
        "output_folder": str(tmp / "out"), "user_name": "Taylor",
    }))
    mods = [null_sink(OUT), null_sink(MIC)]
    procs: list[subprocess.Popen] = []
    server = None
    sources = {"input": MIC, "output": OUT, "scope": "all", "apps": []}
    try:
        # --- crash mid-recording --------------------------------------------------
        server = serve(env)
        procs = players()
        sid = httpx.post(BASE + "/sessions", json=sources).json()["id"]
        time.sleep(60)
        server.send_signal(signal.SIGKILL)
        server.wait()
        for p in procs:
            p.terminate()
        time.sleep(1)
        orphans = subprocess.run(["pgrep", "-f", f"whisper-server.*--port 4598"], capture_output=True, text=True).stdout.split()
        check("no helper processes left behind after the crash", not orphans, f"pids {orphans}" if orphans else "")
        before = sum(1 for _ in open(tmp / "sessions" / sid / "segments.jsonl"))

        server = serve(env)
        s = next(x for x in httpx.get(BASE + "/sessions").json() if x["id"] == sid)
        check("unfinished session detected on relaunch", s["status"] == "interrupted", s["status"])
        httpx.post(f"{BASE}/sessions/{sid}/recover").raise_for_status()
        d = wait_idle(sid)
        last = max((l["end"] for l in d["lines"]), default=0)
        check("recovered to the naming step", d["status"] == "naming", f"{d['status']} {d.get('error') or ''}")
        check("audio kept up to the crash", 55 <= d["duration"] <= 62, f"{d['duration']}s")
        check("transcript finished after the crash", last > 45, f"last line ends at {last:.0f}s; {before} segments were saved before the kill")
        check("speakers separated after recovery", len(d["speakers"]) >= 2, f"{len(d['speakers'])} speakers")

        # --- name + remember, finalise ----------------------------------------------
        first = d["speakers"][0]["id"]
        httpx.put(f"{BASE}/sessions/{sid}/speakers/{first}", json={"name": "Anna Recovery", "remember_voice": True}).raise_for_status()
        fin = httpx.post(f"{BASE}/sessions/{sid}/finalize", json={"title": "Recovered call"}).json()
        md_before = Path(fin["output_path"]).read_text()
        check("finalised with the name", "Anna Recovery:" in md_before, fin["output_path"])
        voices = httpx.get(BASE + "/voiceprints").json()
        check("voice remembered only for the ticked speaker", [v["name"] for v in voices] == ["Anna Recovery"], str(voices))

        # --- quality pass --------------------------------------------------------------
        httpx.post(f"{BASE}/sessions/{sid}/improve", json={"engine": "local_large"}).raise_for_status()
        d2 = wait_idle(sid)
        md_after = Path(d2["output_path"]).read_text()
        check("improve re-transcribed with the other model", d2["improved_with"] == "whisper.cpp:medium", str(d2["improved_with"]))
        check("improve kept the speaker names", "Anna Recovery:" in md_after and d2["speakers"][0]["name"] == "Anna Recovery")
        check("previous version kept as backup",
              (tmp / "sessions" / sid / "transcript.prev.json").exists() and (tmp / "sessions" / sid / "export.prev.md").exists())
        check("transcript was replaced", md_after != md_before and len(d2["lines"]) > 3, f"{len(d2['lines'])} lines")

        # --- second call: the remembered voice is suggested ------------------------------
        procs = players()
        sid2 = httpx.post(BASE + "/sessions", json=sources).json()["id"]
        time.sleep(45)
        httpx.post(f"{BASE}/sessions/{sid2}/stop", json={"speakers": "auto"}).raise_for_status()
        for p in procs:
            p.terminate()
        d3 = wait_idle(sid2)
        suggested = [s["suggested"] for s in d3["speakers"]]
        check("remembered voice suggested on the next call", suggested.count("Anna Recovery") == 1, f"suggestions {suggested}")
        httpx.delete(BASE + "/voiceprints/Anna Recovery").raise_for_status()
        httpx.post(f"{BASE}/sessions/{sid2}/diarize", json={"speakers": "auto"}).raise_for_status()
        d4 = wait_idle(sid2)
        check("forgotten voice is no longer suggested", all(s["suggested"] is None for s in d4["speakers"]))

        # --- delete audio leaves a working transcript --------------------------------------
        httpx.delete(f"{BASE}/sessions/{sid2}/audio").raise_for_status()
        d5 = httpx.get(f"{BASE}/sessions/{sid2}").json()
        r = httpx.post(f"{BASE}/sessions/{sid2}/improve", json={"engine": "local_large"})
        check("delete audio keeps the transcript and blocks improve", not d5["has_audio"] and len(d5["lines"]) > 0 and r.status_code == 400, r.json().get("detail", ""))
    finally:
        for p in procs:
            p.terminate()
        if server and server.poll() is None:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        for m in mods:
            subprocess.run(["pactl", "unload-module", m])
    ok = all(results)
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
