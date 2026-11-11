"""What happens when things go wrong: failed writes, failed starts, a full disk."""

import errno
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from aku import config
from aku.asr.base import Word
from aku.asr.chunker import Chunk
from aku.asr.live import Transcriber
from aku.audio import wav
from aku.audio.capture import SR, ClockedTrack
from aku.events import EventBus
from aku.sessions import manager as manager_module
from aku.sessions.manager import Invalid, Manager
from aku.sessions.store import all_sessions


def block(n=1024):
    return np.full(n, 1000, dtype=np.int16)


def no_space(*args):
    raise OSError(errno.ENOSPC, "No space left on device")


# -- capture ---------------------------------------------------------------------------


def test_failed_write_stops_the_track_instead_of_killing_the_capture_thread(tmp_path, caplog):
    now = [100.0]
    t = ClockedTrack(tmp_path / "mic.wav", now[0], lambda: now[0])
    now[0] += 0.1
    t.feed(block(), now[0])
    written = t.written
    t.wav.write = no_space
    now[0] += 0.1
    with caplog.at_level(logging.ERROR):
        t.feed(block(), now[0])  # must not raise
    assert "No space left on device" in t.error
    assert "mic.wav" in caplog.text and "No space left" in caplog.text
    now[0] += 0.1
    t.feed(block(), now[0])  # later blocks are ignored quietly
    t.tick(now[0] + 5)
    assert t.written == written
    t.finish(now[0] + 6)  # and closing still works
    assert wav.frames(tmp_path / "mic.wav") == written
    import soundfile as sf

    assert sf.info(tmp_path / "mic.wav").frames == written  # header matches, so any player can open it


def test_a_failing_listener_is_dropped_and_recording_goes_on(tmp_path, caplog):
    now = [100.0]
    t = ClockedTrack(tmp_path / "t.wav", now[0], lambda: now[0])
    seen = []
    t.listeners.append(lambda pcm, start: 1 / 0)
    t.listeners.append(lambda pcm, start: seen.append(start))
    with caplog.at_level(logging.ERROR):
        for _ in range(3):
            now[0] += 1024 / SR
            t.feed(block(), now[0])
    assert t.error is None and t.written > 2 * 1024
    assert len(t.listeners) == 1 and len(seen) >= 3  # the healthy listener kept receiving
    assert caplog.text.count("a listener failed") == 1


# -- transcriber -------------------------------------------------------------------------


def test_transcriber_survives_a_segment_that_cannot_be_stored():
    class Backend:
        name, last_language = "fake", None
        def transcribe(self, audio, prompt="", language="en"):
            return [Word(0.1, 0.5, "hello")]

    stored, errors = [], []

    def on_segment(seg):
        if not stored and not errors:
            no_space()
        stored.append(seg)

    t = Transcriber(Backend(), on_segment, on_error=errors.append)
    for start in (0, SR * 5):
        t._queue.put(Chunk("mic", start, np.zeros(SR, dtype=np.int16), start / SR, start / SR + 1, start / SR + 1, 1.0))
    t.finish()
    assert len(stored) == 1 and len(errors) == 1 and "Could not save part of the transcript" in errors[0]


# -- event bus ---------------------------------------------------------------------------


def test_errors_shown_to_the_user_are_also_logged(caplog):
    bus = EventBus()
    bus.subscribe(lambda e: 1 / 0)  # a broken subscriber must not hide the event from the others
    got = []
    bus.subscribe(got.append)
    with caplog.at_level(logging.WARNING):
        bus.emit("error", session="20261111-120000", message="No OpenAI API key set.")
    assert got[0]["message"] == "No OpenAI API key set."
    assert "[20261111-120000] No OpenAI API key set." in caplog.text
    assert "an event subscriber failed" in caplog.text


# -- session manager, with capture and transcription faked ----------------------------------


class FakeTrack:
    def __init__(self):
        self.listeners, self.level, self.error = [], 0.1, None
        self.wav = SimpleNamespace(flush=lambda: None)


class FakeCapture:
    explode = False

    def __init__(self, session_dir, sources, tag):
        if FakeCapture.explode:
            raise RuntimeError("PipeWire is not running")
        self.mic, self.system = FakeTrack(), FakeTrack()
        self.warnings, self.problem = [], None
        self.t0 = time.monotonic()
        for name in ("mic", "system"):
            w = wav.WavWriter(Path(session_dir) / f"{name}.wav")
            w.write(np.zeros(SR, dtype=np.int16))
            w.close()

    tracks = property(lambda self: {"mic": self.mic, "system": self.system})
    failed = property(lambda self: self.problem)

    def start(self): pass
    def elapsed(self): return time.monotonic() - self.t0
    def stop(self): return self.elapsed()
    def errors(self): return {}


@pytest.fixture
def m(monkeypatch):
    FakeCapture.explode = False
    config.save_settings({"live_diarization": False})
    monkeypatch.setattr(manager_module, "Capture", FakeCapture)
    monkeypatch.setattr(Transcriber, "chunker", lambda self, track: SimpleNamespace(feed=lambda pcm, start=None: None))
    mgr = Manager()
    monkeypatch.setattr(mgr, "make_backend", lambda *a, **k: SimpleNamespace(name="fake", close=lambda: None))
    return mgr


def until_processed(m, sid: str) -> dict:
    """Wait for a session that stopped by itself to finish processing."""
    for _ in range(100):
        d = m.detail(sid)
        if d["status"] not in ("recording", "processing"):
            return d
        time.sleep(0.05)
    return m.detail(sid)


def test_start_and_stop(m, caplog):
    with caplog.at_level(logging.INFO):
        sid = m.start()["id"]
        assert m.status()["recording"] and m.detail(sid)["status"] == "recording"
        m.stop(sid)
        m.wait(sid)
    d = m.detail(sid)
    assert d["status"] == "naming" and d["error"] is None and not m.status()["recording"]
    assert f"recording {sid} started" in caplog.text and f"recording {sid} stopped" in caplog.text


def test_a_failed_start_leaves_no_session_behind(m, caplog):
    FakeCapture.explode = True
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
        m.start()
    assert all_sessions() == [] and m.active is None and not m.status()["recording"]
    assert "could not start recording" in caplog.text
    FakeCapture.explode = False
    assert m.start()["status"] == "recording"  # and the next attempt works


def test_recording_ends_itself_when_the_audio_cannot_be_written(m):
    events = []
    m.bus.subscribe(events.append)
    sid = m.start()["id"]
    m.active.capture.problem = "Could not write the recording: No space left on device"
    for _ in range(40):
        if not m.status()["recording"]:
            break
        time.sleep(0.1)
    assert not m.status()["recording"]  # the UI no longer claims to be recording
    d = until_processed(m, sid)
    assert d["status"] == "naming"  # what was recorded is kept and processed
    assert "Recording stopped early" in d["error"] and "No space left on device" in d["error"]
    assert any(e["type"] == "error" and "stopped early" in e["message"] for e in events)


def test_recording_ends_cleanly_before_the_disk_is_full(m, monkeypatch):
    monkeypatch.setattr(manager_module, "DISK_CHECK_TICKS", 1)
    sid = m.start()["id"]
    monkeypatch.setattr(config, "free_bytes", lambda path: 50 * 1024 * 1024)
    for _ in range(40):
        if not m.status()["recording"]:
            break
        time.sleep(0.1)
    assert "disk is almost full" in until_processed(m, sid)["error"]


def test_no_recording_starts_on_a_nearly_full_disk(m, monkeypatch):
    monkeypatch.setattr(config, "free_bytes", lambda path: 100 * 1024 * 1024)
    with pytest.raises(Invalid, match="100 MB of disk space left"):
        m.start()
    assert all_sessions() == []
