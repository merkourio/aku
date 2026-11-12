"""Two-track capture locked to one session clock.

Each track reads raw PCM from its own pw-record process. ClockedTrack keeps
"sample position == session time": it pads silence when a stream starts late,
stalls or drops out, and nudges out slow drift between the audio clock and
the session clock.
"""

from __future__ import annotations

import fcntl
import logging
import os
import select
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from ..config import SAMPLE_RATE, die_with_parent
from . import pipewire
from .wav import WavWriter, repair

SR = SAMPLE_RATE
log = logging.getLogger(__name__)
GAP = int(0.12 * SR)  # a block this late (about two blocks) means the stream stalled: pad right away
DRIFT = int(0.02 * SR)  # offset beyond this is corrected
WINDOW = 10.0  # seconds over which the offset is measured
RESYNC_WINDOW = 1.0  # shorter first window after a start or a gap
STALL_AFTER = 0.4  # seconds without data before silence is written
STALL_KEEP = int(0.25 * SR)  # stay this far behind the clock while padding a stall

Listener = Callable[[np.ndarray, int], None]  # (mono int16 samples, start sample)


class ClockedTrack:
    def __init__(self, path: Path, t0: float, clock: Callable[[], float] = time.monotonic):
        self.wav = WavWriter(path, SR)
        self.t0 = t0
        self.clock = clock
        self.written = 0
        self.listeners: list[Listener] = []
        self.padded = 0  # samples of silence inserted
        self.dropped = 0  # samples removed (audio clock ran fast)
        self.level = 0.0  # recent peak, 0..1
        self.error: str | None = None  # set when writing the file failed
        self._win_start = t0
        self._win_len = RESYNC_WINDOW
        self._win_min: int | None = None
        self._resync = True  # next block re-anchors the track to the clock
        self._last_flush = t0
        self._lock = threading.Lock()
        self._closed = False

    def _fail(self, e: OSError) -> None:
        """Writing failed (disk full, folder gone). Stop this track; the session manager ends the recording."""
        if self.error is None:
            self.error = f"Could not write the recording: {e.strerror or e}"
            log.error("%s: %s", self.wav.path.name, self.error)
        self._closed = True

    def _emit(self, pcm: np.ndarray) -> None:
        if len(pcm) == 0 or self.error:
            return
        start = self.written
        self.wav.write(pcm)
        self.written += len(pcm)
        for fn in list(self.listeners):
            try:
                fn(pcm, start)
            except Exception:  # noqa: BLE001 - e.g. the transcriber's chunker; the audio must keep recording
                log.exception("%s: a listener failed and was dropped; audio is still recorded", self.wav.path.name)
                self.listeners.remove(fn)

    def _silence(self, n: int) -> None:
        if n > 0:
            self.padded += n
            self._emit(np.zeros(n, dtype=np.int16))

    def _new_window(self, now: float, length: float) -> None:
        self._win_min = None
        self._win_start = now
        self._win_len = length

    def feed(self, pcm: np.ndarray, now: float | None = None) -> None:
        """A block of mono samples that arrived at `now`."""
        with self._lock:
            if self._closed or len(pcm) == 0:
                return
            try:
                self._feed(pcm, self.clock() if now is None else now)
            except OSError as e:
                self._fail(e)

    def _feed(self, pcm: np.ndarray, now: float) -> None:
        deficit = int((now - self.t0) * SR) - (self.written + len(pcm))
        if deficit > GAP or (self._resync and deficit > 0):
            self._silence(deficit)
            deficit = 0
            self._resync = True
        if self._resync:
            self._new_window(now, RESYNC_WINDOW)
            self._resync = False
        self._win_min = deficit if self._win_min is None else min(self._win_min, deficit)
        if now - self._win_start >= self._win_len:
            # Arrival jitter only ever adds delay, so the least-delayed block in a
            # window shows the true offset between audio clock and session clock.
            off = self._win_min
            if off > DRIFT:
                self._silence(off)
            elif off < -DRIFT:
                cut = min(-off, len(pcm))
                pcm = pcm[cut:]
                self.dropped += cut
            self._new_window(now, WINDOW)
        if len(pcm):
            self.level = float(np.abs(pcm).max()) / 32768.0
        self._emit(pcm)
        if now - self._last_flush > 1.0:
            self.wav.flush()
            self._last_flush = now

    def tick(self, now: float | None = None) -> None:
        """Called while no data arrives: keep the timeline moving with silence."""
        with self._lock:
            if self._closed:
                return
            now = self.clock() if now is None else now
            deficit = int((now - self.t0) * SR) - self.written
            if deficit > int(STALL_AFTER * SR):
                try:
                    self._silence(deficit - STALL_KEEP)
                    self.wav.flush()
                except OSError as e:
                    self._fail(e)
                    return
                self.level = 0.0
                self._resync = True

    def finish(self, now: float | None = None) -> None:
        """Pad to the stop time and close the file."""
        with self._lock:
            try:
                if not self._closed:
                    now = self.clock() if now is None else now
                    self._silence(int((now - self.t0) * SR) - self.written)
            except OSError as e:
                self._fail(e)
            self._closed = True
            try:
                self.wav.close()
            except OSError as e:
                log.error("%s: could not close cleanly: %s", self.wav.path.name, e)
                try:  # the header only needs rewriting in place, which works on a full disk too
                    repair(self.wav.path, SR)
                except OSError:
                    log.error("%s: header not repaired; Recover will do it", self.wav.path.name)


class PwStream(threading.Thread):
    """Runs pw-record and feeds a ClockedTrack. Restarts the process if it dies."""

    def __init__(self, track: ClockedTrack, node_name: str, target: str | None, capture_sink: bool, no_autoconnect: bool):
        super().__init__(daemon=True, name=node_name)
        self.track = track
        self.node_name = node_name
        self.target = target
        self.capture_sink = capture_sink
        self.no_autoconnect = no_autoconnect
        self._stop_flag = threading.Event()
        self._proc: subprocess.Popen | None = None
        self.error: str | None = None

    def command(self) -> list[str]:
        props = [f"node.name={self.node_name}", "application.name=Aku", 'node.description="Aku recording"']
        if self.capture_sink:
            props.append("stream.capture.sink=true")
        cmd = ["pw-record", "-P", "{ " + " ".join(props) + " }"]
        if self.no_autoconnect:
            cmd += ["--target", "0"]
        elif self.target:
            cmd += ["--target", self.target]
        cmd += ["--rate", str(SR), "--channels", "2", "--format", "s16", "--raw", "--latency", "40ms", "-"]
        return cmd

    def run(self) -> None:
        while not self._stop_flag.is_set():
            try:
                self._proc = subprocess.Popen(
                    self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                    preexec_fn=die_with_parent,
                )
            except OSError as e:
                self.error = f"cannot start pw-record: {e}"
                log.error("%s: %s", self.node_name, self.error)
                return
            fd = self._proc.stdout.fileno()
            try:
                fcntl.fcntl(fd, 1031, 1 << 20)  # F_SETPIPE_SZ: room for a ~16 s backlog
            except OSError:
                pass
            rest = b""
            while not self._stop_flag.is_set():
                ready, _, _ = select.select([fd], [], [], 0.25)
                if not ready:
                    self.track.tick()
                    if self._proc.poll() is not None:
                        break
                    continue
                data = os.read(fd, 1 << 20)
                if not data:
                    break
                # Drain any backlog first, so a late read is not mistaken for a gap.
                while select.select([fd], [], [], 0)[0]:
                    more = os.read(fd, 1 << 20)
                    if not more:
                        break
                    data += more
                now = self.track.clock()
                data = rest + data
                usable = len(data) // 4 * 4
                rest = data[usable:]
                if usable:
                    stereo = np.frombuffer(data[:usable], dtype="<i2").reshape(-1, 2)
                    mono = (stereo.astype(np.int32).sum(axis=1) // 2).astype(np.int16)
                    self.track.feed(mono, now)
            if self._stop_flag.is_set():
                break
            err = b""
            try:
                err = self._proc.stderr.read() or b""
            except Exception:
                pass
            self.error = err.decode(errors="replace").strip()[-300:] or "pw-record exited"
            log.warning("%s: pw-record stopped (%s); retrying", self.node_name, self.error)
            self._kill()
            # PipeWire restarting or device vanished: retry, the gap is padded by tick().
            for _ in range(10):
                if self._stop_flag.wait(0.1):
                    break
                self.track.tick()
        self._kill()

    def _kill(self) -> None:
        p = self._proc
        if p and p.poll() is None:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()

    def stop(self) -> None:
        self._stop_flag.set()


class AppLinker(threading.Thread):
    """Keeps the chosen apps' audio streams linked into our capture node."""

    def __init__(self, capture_node: str, app_keys: list[str], interval: float = 1.0):
        super().__init__(daemon=True, name="aku-linker")
        self.capture_node = capture_node
        self.app_keys = app_keys
        self.interval = interval
        self._stop_flag = threading.Event()
        self.linked = 0
        self.polls = 0
        self._last_error: str | None = None

    def run(self) -> None:
        while not self._stop_flag.is_set():
            try:
                g = pipewire.snapshot()
                for out_port, in_port in pipewire.wanted_links(g, self.capture_node, self.app_keys):
                    if pipewire.link(out_port, in_port):
                        self.linked += 1
                        log.info("linked an app stream into %s", self.capture_node)
                self._last_error = None
            except Exception as e:  # noqa: BLE001 - PipeWire restarting; try again next round
                if str(e) != self._last_error:  # once per distinct problem, not once a second
                    log.warning("could not update app links for %s: %s", self.capture_node, e)
                    self._last_error = str(e)
            self.polls += 1
            self._stop_flag.wait(0.2 if self.polls < 10 else self.interval)

    def stop(self) -> None:
        self._stop_flag.set()


class Capture:
    """Mic + system tracks for one session."""

    def __init__(self, session_dir: Path, sources: dict, tag: str):
        self.dir = Path(session_dir)
        self.sources = sources
        self.t0 = time.monotonic()
        self.mic = ClockedTrack(self.dir / "mic.wav", self.t0)
        self.system = ClockedTrack(self.dir / "system.wav", self.t0)
        scope_apps = sources.get("scope") == "apps" and sources.get("apps")
        silent = sources.get("scope") == "none"  # interview without computer audio: nothing is linked in
        self.warnings: list[str] = []
        mic_target, mic_is_sink = self._resolve(sources.get("input"), "Audio/Source", "microphone")
        out_target, _ = self._resolve(sources.get("output"), "Audio/Sink", "output device")
        self.mic_stream = PwStream(self.mic, f"aku-mic-{tag}", mic_target, mic_is_sink, False)
        self.system_stream = PwStream(
            self.system,
            f"aku-system-{tag}",
            None if scope_apps or silent else out_target,
            capture_sink=not (scope_apps or silent),
            no_autoconnect=bool(scope_apps or silent),
        )
        self.linker = AppLinker(f"aku-system-{tag}", list(sources["apps"])) if scope_apps else None
        self.stopped_at: float | None = None

    def _resolve(self, name: str | None, media_class: str, what: str) -> tuple[str | None, bool]:
        """A remembered device may be gone: fall back to the default instead of failing."""
        if not name:
            return None, False
        try:
            node = pipewire.snapshot().by_name(name)
        except Exception:  # noqa: BLE001
            return name, False
        if node is None:
            self.warnings.append(f"The chosen {what} is not connected; using the default one.")
            return None, False
        # an output device picked as the "mic" means: listen to what it plays
        return name, node.media_class == "Audio/Sink" and media_class == "Audio/Source"

    @property
    def tracks(self) -> dict[str, ClockedTrack]:
        return {"mic": self.mic, "system": self.system}

    def start(self) -> None:
        self.mic_stream.start()
        self.system_stream.start()
        if self.linker:
            self.linker.start()

    def elapsed(self) -> float:
        return (self.stopped_at or time.monotonic()) - self.t0

    def stop(self) -> float:
        """Stop both streams; returns the session duration in seconds."""
        self.stopped_at = time.monotonic()
        for t in (self.mic, self.system):
            t.finish(self.stopped_at)  # blocks arriving after this moment are ignored
        if self.linker:
            self.linker.stop()
        for s in (self.mic_stream, self.system_stream):
            s.stop()
        for s in (self.mic_stream, self.system_stream):
            s.join(timeout=5)
        return self.stopped_at - self.t0

    @property
    def failed(self) -> str | None:
        """Why the recording cannot continue (a track could not be written), if so."""
        return self.mic.error or self.system.error

    def errors(self) -> dict[str, str]:
        out = {}
        if self.mic.error or self.mic_stream.error:
            out["mic"] = self.mic.error or self.mic_stream.error
        if self.system.error or self.system_stream.error:
            out["system"] = self.system.error or self.system_stream.error
        return out


def record_clip(path: Path, seconds: float, target: str | None = None, capture_sink: bool = False) -> float:
    """Record the microphone for a fixed time into a WAV (voice enrolment). Returns the seconds recorded.

    capture_sink: the target is an output device, so record what it plays.
    """
    cmd = ["pw-record"] + (["-P", "{ stream.capture.sink=true }"] if capture_sink else [])
    cmd += ["--target", target] if target else []
    cmd += ["--rate", str(SR), "--channels", "1", "--format", "s16", "--raw", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, preexec_fn=die_with_parent)
    fd = proc.stdout.fileno()
    want = int(seconds * SR) * 2
    data = bytearray()
    deadline = time.monotonic() + seconds + 5
    try:
        while len(data) < want and time.monotonic() < deadline:
            if select.select([fd], [], [], 0.25)[0]:
                block = os.read(fd, 1 << 16)
                if not block:
                    break
                data += block
            elif proc.poll() is not None:
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    pcm = np.frombuffer(bytes(data[: min(len(data), want) // 2 * 2]), dtype="<i2")
    writer = WavWriter(path, SR)
    writer.write(pcm)
    writer.close()
    return len(pcm) / SR
