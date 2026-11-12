"""A session's folder is the source of truth for that session."""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime
from pathlib import Path

from .. import config
from ..asr.live import Segment

STATUSES = ("recording", "processing", "naming", "finalised", "interrupted", "error")


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


class Session:
    def __init__(self, sid: str):
        self.id = sid
        self.dir = config.sessions_dir() / sid
        self._lock = threading.Lock()

    # paths
    @property
    def mic(self) -> Path: return self.dir / "mic.wav"
    @property
    def system(self) -> Path: return self.dir / "system.wav"
    @property
    def meta_path(self) -> Path: return self.dir / "meta.json"
    @property
    def segments_path(self) -> Path: return self.dir / "segments.jsonl"
    @property
    def diarization_path(self) -> Path: return self.dir / "diarization.json"
    @property
    def mic_diarization_path(self) -> Path: return self.dir / "diarization.mic.json"  # interview mode
    @property
    def speakers_path(self) -> Path: return self.dir / "speakers.json"
    @property
    def transcript_path(self) -> Path: return self.dir / "transcript.json"
    @property
    def temp_md(self) -> Path: return self.dir / "transcript.md"

    def track(self, name: str) -> Path:
        return self.mic if name == "mic" else self.system

    def exists(self) -> bool:
        return self.meta_path.exists()

    @property
    def has_audio(self) -> bool:
        return self.mic.exists() and self.system.exists()

    # meta
    @property
    def meta(self) -> dict:
        return read_json(self.meta_path, {}) or {}

    def update(self, **changes) -> dict:
        with self._lock:
            meta = self.meta
            meta.update(changes)
            write_json(self.meta_path, meta)
            return meta

    # segments
    def append_segment(self, seg: Segment) -> None:
        with self._lock, open(self.segments_path, "a") as f:
            f.write(json.dumps(seg.to_json(), ensure_ascii=False) + "\n")
            f.flush()

    def segments(self) -> list[Segment]:
        out = []
        try:
            lines = self.segments_path.read_text().splitlines()
        except OSError:
            return out
        for line in lines:
            try:
                out.append(Segment.from_json(json.loads(line)))
            except (ValueError, KeyError):
                continue  # a line cut short by a crash
        return out

    # other files
    @property
    def diarization(self) -> dict | None:
        return read_json(self.diarization_path)

    @property
    def mic_diarization(self) -> dict | None:
        return read_json(self.mic_diarization_path)

    def diarization_path_for(self, track: str) -> Path:
        return self.mic_diarization_path if track == "mic" else self.diarization_path

    def diarization_for(self, track: str) -> dict | None:
        return read_json(self.diarization_path_for(track))

    @property
    def speakers(self) -> dict:
        return read_json(self.speakers_path, {"speakers": {}}) or {"speakers": {}}

    def save_speakers(self, data: dict) -> None:
        write_json(self.speakers_path, data)

    @property
    def transcript(self) -> dict:
        return read_json(self.transcript_path, {"lines": []}) or {"lines": []}

    def save_transcript(self, data: dict, keep_previous: bool = False) -> None:
        if keep_previous and self.transcript_path.exists():
            shutil.copy2(self.transcript_path, self.dir / "transcript.prev.json")
        write_json(self.transcript_path, data)

    def delete(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def new_session() -> Session:
    config.ensure_dirs()
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    sid, n = base, 1
    while (config.sessions_dir() / sid).exists():
        n += 1
        sid = f"{base}-{n}"
    s = Session(sid)
    s.dir.mkdir(parents=True)
    return s


def all_sessions() -> list[Session]:
    """The sessions list is built by scanning session folders, newest first."""
    root = config.sessions_dir()
    if not root.exists():
        return []
    out = [Session(p.name) for p in root.iterdir() if (p / "meta.json").exists()]
    return sorted(out, key=lambda s: s.id, reverse=True)
