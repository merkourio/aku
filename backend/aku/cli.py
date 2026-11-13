"""The `aku` command."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, config, log


def base_url() -> str:
    return f"http://127.0.0.1:{config.load_settings()['port']}"


def call(method: str, path: str, body: dict | None = None):
    """Talk to the running service. Exits with a plain message if it is not running."""
    import httpx

    try:
        r = httpx.request(method, base_url() + path, json=body, timeout=30)
    except httpx.ConnectError:
        sys.exit("Aku is not running. Start it with:  aku serve")
    if r.status_code >= 400:
        try:
            sys.exit(f"Aku: {r.json().get('detail', r.text)}")
        except ValueError:
            sys.exit(f"Aku: {r.text}")
    return r.json()


# -- service commands ---------------------------------------------------------------


def cmd_serve(args) -> None:
    import uvicorn

    from .api.server import create_app

    settings = config.load_settings()
    port = args.port or settings["port"]
    if args.port:
        config.save_settings({"port": port})
    app = create_app(port=port, tray=settings["tray"] and not args.no_tray)
    print(f"Aku is running at http://127.0.0.1:{port}/  (Ctrl+C to quit)")
    if args.open:
        subprocess.Popen(["xdg-open", f"http://127.0.0.1:{port}/"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # An open browser tab keeps a WebSocket alive; without a limit, quitting would wait for it forever.
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=3))
    app.state.server = server  # lets open WebSockets notice that Aku is quitting
    server.run()


def cmd_toggle(args) -> None:
    r = call("POST", "/toggle")
    print("Recording started." if r["action"] == "started" else "Recording stopped. Finishing the transcript…")


def cmd_start(args) -> None:
    call("POST", "/sessions", {})
    print("Recording started.")


def cmd_stop(args) -> None:
    status = call("GET", "/status")
    if not status["recording"]:
        sys.exit("Aku is not recording.")
    call("POST", f"/sessions/{status['session']}/stop", {"speakers": args.speakers})
    print("Recording stopped. Finishing the transcript…")


def cmd_status(args) -> None:
    s = call("GET", "/status")
    if args.json:
        print(json.dumps(s, indent=1))
        return
    if s["recording"]:
        m, sec = divmod(int(s["elapsed"]), 60)
        print(f"Recording for {m}:{sec:02d} (session {s['session']}), {s['pending']} chunk(s) waiting")
    else:
        print("Not recording.")
    for sid, job in s["jobs"].items():
        pct = f" {job['progress']*100:.0f}%" if job.get("progress") is not None else ""
        print(f"Working on {sid}: {job['step']}{pct}")


def cmd_open(args) -> None:
    """Open the UI; start the background service first if it is installed but not running."""
    import httpx

    def up() -> bool:
        try:
            httpx.get(base_url() + "/status", timeout=1)
            return True
        except httpx.HTTPError:
            return False

    if not up():
        # restart, not start: also rescues a service that is alive but no longer answering
        subprocess.run(["systemctl", "--user", "restart", "aku.service"], capture_output=True)
        for _ in range(60):
            if up():
                break
            time.sleep(0.25)
        else:
            from .events import notify

            notify("Aku could not start", "Run `aku doctor` in a terminal to see what is missing.")
            sys.exit("Aku is not running. Start it with:  aku serve")
    subprocess.Popen(["xdg-open", base_url() + "/"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# -- standalone commands ---------------------------------------------------------------


def cmd_devices(args) -> None:
    from .audio import pipewire

    d = pipewire.list_devices()
    if args.json:
        print(json.dumps(d, indent=1))
        return
    for title, key in (("Microphones", "inputs"), ("Output devices", "outputs")):
        print(title + ":")
        for dev in d[key]:
            print(f"  {'*' if dev['default'] else ' '} {dev['description']}\n      {dev['name']}")
    print("Apps playing audio (use the key with --app):")
    for app in d["apps"] or []:
        print(f"    {app['label']}   key: {app['key']}")
    if not d["apps"]:
        print("    (none right now)")


def cmd_record(args) -> None:
    """The whole flow in the terminal: record, live transcript, speakers, names, Markdown."""
    from .sessions.manager import Manager

    manager = Manager()
    width = shutil.get_terminal_size().columns

    def on_event(e: dict) -> None:
        if e["type"] == "segment":
            s = e["segment"]
            print(f"\r[{int(s['start'])//60:02d}:{int(s['start'])%60:02d}] {s['name']}: {s['text']}"[: max(width, 40) * 4])
        elif e["type"] == "error":
            print(f"\r! {e['message']}")
        elif e["type"] == "diarization_progress":
            print(f"\r{e['step']} {e['progress']*100:3.0f}%", end="", flush=True)

    manager.bus.subscribe(on_event)
    scope = "apps" if args.app else "all"
    if args.interview and not args.computer_audio:
        scope = "none"  # an in-person interview: the microphone only
    sources = {"input": args.input, "output": args.output, "scope": scope, "apps": args.app or []}
    s = manager.start(sources, backend=args.backend, language=args.language, interview=args.interview,
                      translate=False if args.as_spoken else None)
    sid = s["id"]
    print(f"Recording (session {sid}). Press Ctrl+C to stop.\n")
    try:
        if args.seconds:
            time.sleep(args.seconds)
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    print("\nStopping…")
    speakers, mic_speakers = args.speakers, args.mic_speakers
    if speakers is None and scope != "none":
        answer = input("How many other speakers were on the call? [Enter = detect automatically] ").strip()
        speakers = answer or "auto"
    if mic_speakers is None and args.interview:
        answer = input("How many people were at the microphone, you included? [Enter = detect automatically] ").strip()
        mic_speakers = answer or "auto"
    manager.stop(sid, speakers or "auto", mic_speakers=mic_speakers or "auto")
    manager.wait(sid)
    print()
    d = manager.detail(sid)
    if d["status"] != "naming":
        sys.exit(f"Processing failed: {d.get('error')}")
    mic_people = [sp for sp in d["speakers"] if sp["track"] == "mic"]
    if mic_people and not any(sp["me"] for sp in mic_people):
        print("Aku could not tell which voice at the microphone is yours (run `aku enrol` once to teach it).")
    for sp in d["speakers"]:
        if sp["me"]:
            print(f"{sp['display']} (you) spoke for {sp['duration']:.0f}s.")
            continue
        while True:
            if sp["track"] == "mic" and not any(x["me"] for x in mic_people):
                play = manager.session(sid).dir / "clip.wav"
                answer = input(f"{sp['display']} spoke for {sp['duration']:.0f}s. Is this you? [y/N, p = play clip] ").strip().lower()
                if answer == "p":
                    play.write_bytes(manager.speaker_clip(sid, sp["id"]))
                    subprocess.run(["pw-play", str(play)])
                    play.unlink(missing_ok=True)
                    continue
                if answer == "y":
                    manager.set_speaker(sid, sp["id"], None, me=True)
                    sp["me"] = True
                    break
            hint = f" [{sp['suggested']}]" if sp["suggested"] else ""
            answer = input(f"{sp['display']} spoke for {sp['duration']:.0f}s. Name{hint} (p = play clip, Enter = {'accept' if sp['suggested'] else 'skip'}): ").strip()
            if answer.lower() == "p":
                clip = manager.session(sid).dir / "clip.wav"
                clip.write_bytes(manager.speaker_clip(sid, sp["id"]))
                subprocess.run(["pw-play", str(clip)])
                clip.unlink(missing_ok=True)
                continue
            name = answer or sp["suggested"]
            if name:
                remember = input(f"  Remember {name}'s voice for next time? [y/N] ").strip().lower() == "y"
                manager.set_speaker(sid, sp["id"], name, remember)
            break
    title = args.title or input("Title for this transcript: ").strip()
    final = manager.finalize(sid, title or None, args.out)
    print(f"\nSaved: {final['output_path']}")


def cmd_enrol(args) -> None:
    """Teach Aku the owner's voice, so interview mode knows who at the microphone is you."""
    from .diarize import voiceprints
    from .sessions.manager import Manager

    if args.forget:
        print("Forgotten." if voiceprints.forget_me() else "No voice sample was saved.")
        return
    print(f"Read anything aloud in your normal voice for {args.seconds:.0f} seconds. Recording starts now…")
    try:
        info = Manager().enrol_me(args.seconds, args.input)
    except (ValueError, RuntimeError) as e:
        sys.exit(f"Aku: {e}")
    print("Saved your voice for:", ", ".join(i["space"] for i in info))


UNIT = Path.home() / ".config" / "systemd" / "user" / "aku.service"
DESKTOP = Path.home() / ".local" / "share" / "applications" / "aku.desktop"
ICON = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "aku.svg"


def cmd_setup(args) -> None:
    """The background service (starts at login) and the launcher entry, for the installed `aku`."""
    def systemctl(*a: str) -> bool:
        return subprocess.run(["systemctl", "--user", *a], capture_output=True).returncode == 0

    if args.remove:
        systemctl("disable", "--now", "aku.service")
        for p in (UNIT, DESKTOP, ICON):
            p.unlink(missing_ok=True)
        systemctl("daemon-reload")
        print("Removed the service and the launcher entry.")
        return
    exe = shutil.which("aku") or str(Path(sys.argv[0]).resolve())
    for p in (UNIT, DESKTOP, ICON):
        p.parent.mkdir(parents=True, exist_ok=True)
    icon = Path(__file__).resolve().parent / "static" / "favicon.svg"
    if icon.exists():
        shutil.copyfile(icon, ICON)
    UNIT.write_text(f"""[Unit]
Description=Aku call transcriber
After=graphical-session.target
PartOf=graphical-session.target

[Service]
UMask=0077
ExecStart={exe} serve
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
""")
    DESKTOP.write_text(f"""[Desktop Entry]
Type=Application
Name=Aku
Comment=Record and transcribe calls locally
Exec={exe} open
Icon=aku
Categories=AudioVideo;Audio;Utility;
Terminal=false
""")
    if systemctl("daemon-reload") and systemctl("enable", "aku.service") and systemctl("restart", "aku.service"):
        print("Aku runs in the background and starts at every login.")
    else:
        print("Could not set up the background service (no systemd user session?). Start Aku with:  aku serve")


def cmd_bench(args) -> None:
    from .asr import bench

    wav_path = Path(args.wav).expanduser()
    if not wav_path.exists():
        sys.exit(f"No such file: {wav_path}  (it must be a 16 kHz mono WAV)")
    results = bench.run(wav_path, args.models or None, use_gpu=not args.cpu)
    best = bench.pick(results)
    print()
    if best:
        print(f"Recommended default model: {best} (first one at or under 0.5x real time)")
        if args.apply:
            config.save_settings({"local_model": best})
            print("Saved as the local model.")
    else:
        print("No model ran at 0.5x real time or better. Consider the cloud backend.")


def cmd_doctor(args) -> None:
    """Plain checks of everything Aku depends on."""
    def line(ok: bool | None, text: str) -> None:
        print(f"  {'ok ' if ok else ('-- ' if ok is None else 'NO ')} {text}")

    settings = config.load_settings()
    print("Aku", __version__)
    for tool in ("pw-record", "pw-dump", "pw-link"):
        line(shutil.which(tool) is not None, f"{tool} (PipeWire tools)")
    server = config.whisper_bin("whisper-server")
    line(server is not None, f"whisper-server: {server or 'not built - run scripts/build-whisper.sh'}")
    model = config.model_path(settings["local_model"])
    line(model.exists(), f"model {settings['local_model']}: {model if model.exists() else 'not downloaded yet (downloads on first use)'}")
    line(config.vad_model_path().exists() or None, "voice-activity model (downloads on first use)")
    try:
        gpu = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=10).stdout
        names = [l.split("=")[1].strip() for l in gpu.splitlines() if "deviceName" in l]
        line(bool(names), f"Vulkan GPU: {', '.join(names) or 'none found'}")
    except (OSError, subprocess.TimeoutExpired):
        line(None, "vulkaninfo not available (GPU not checked)")
    line(bool(config.get_secret("hf_token")) or None,
         "Hugging Face token (optional: enables pyannote; without it the built-in speaker separation is used)")
    line(bool(config.get_secret("openai_api_key")) or None, "OpenAI API key (optional: cloud backend)")
    line(shutil.which("notify-send") is not None, "notify-send (desktop notifications)")
    try:
        import httpx

        httpx.get(base_url() + "/status", timeout=2)
        line(True, f"service running at {base_url()}/")
    except Exception:  # noqa: BLE001
        line(None, "service not running (start with: aku serve)")
    print(f"  data: {config.data_dir()}\n  transcripts go to: {settings['output_folder']}")


def main(argv: list[str] | None = None) -> None:
    os.umask(0o077)  # recordings/voice data must not be world-readable
    ap = argparse.ArgumentParser(prog="aku", description="Aku: local call transcriber.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the service and the browser UI")
    p.add_argument("--port", type=int)
    p.add_argument("--no-tray", action="store_true")
    p.add_argument("--open", action="store_true", help="open the UI in the browser")
    p.set_defaults(fn=cmd_serve)

    sub.add_parser("toggle", help="start recording with the last settings, or stop (bind this to a hotkey)").set_defaults(fn=cmd_toggle)
    sub.add_parser("start", help="start recording with the last settings").set_defaults(fn=cmd_start)
    p = sub.add_parser("stop", help="stop recording")
    p.add_argument("--speakers", default="auto", help='"auto" or the number of other speakers')
    p.set_defaults(fn=cmd_stop)
    p = sub.add_parser("status", help="what the service is doing")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)
    sub.add_parser("open", help="open the UI in the browser").set_defaults(fn=cmd_open)

    p = sub.add_parser("devices", help="list microphones, outputs and apps playing audio")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_devices)

    p = sub.add_parser("record", help="record and transcribe in the terminal, without the service")
    p.add_argument("--input", help="microphone name (default: system default)")
    p.add_argument("--output", help="output device name (default: system default)")
    p.add_argument("--app", action="append", help="only capture this app (key from `aku devices`); repeatable")
    p.add_argument("--speakers", help='"auto" or the number of other speakers (asked if omitted)')
    p.add_argument("--backend", choices=["local", "cloud"])
    p.add_argument("--language", help='language code such as en or da, or "auto" (default: your last choice)')
    p.add_argument("--as-spoken", action="store_true",
                   help="with a language chosen: keep speech in other languages as spoken instead of writing it in that language")
    p.add_argument("--seconds", type=float, help="stop by itself after this long")
    p.add_argument("--interview", action="store_true",
                   help="several people share the microphone: tell them apart too; records the microphone only")
    p.add_argument("--computer-audio", action="store_true",
                   help="with --interview: also record the computer's audio (everyone in the room must wear headphones)")
    p.add_argument("--mic-speakers", help='with --interview: "auto" or the number of people at the microphone, you included')
    p.add_argument("--title")
    p.add_argument("--out", help="output folder (default from settings)")
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("enrol", help="record your own voice once, so interview mode knows which speaker is you")
    p.add_argument("--seconds", type=float, default=30)
    p.add_argument("--input", help="microphone name (default: the one you last recorded with)")
    p.add_argument("--forget", action="store_true", help="delete your saved voice sample")
    p.set_defaults(fn=cmd_enrol)

    p = sub.add_parser("setup", help="set up the background service and the launcher entry (the installers run this)")
    p.add_argument("--remove", action="store_true", help="remove them again")
    p.set_defaults(fn=cmd_setup)

    p = sub.add_parser("bench", help="measure transcription speed of the local models")
    p.add_argument("wav", help="a 16 kHz mono WAV, ideally about 10 minutes")
    p.add_argument("--models", nargs="*")
    p.add_argument("--cpu", action="store_true", help="benchmark without the GPU")
    p.add_argument("--apply", action="store_true", help="save the recommended model as the default")
    p.set_defaults(fn=cmd_bench)

    sub.add_parser("doctor", help="check that everything Aku needs is in place").set_defaults(fn=cmd_doctor)

    args = ap.parse_args(argv)
    log.setup()
    try:
        args.fn(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
