"""Improve engine: OpenAI's diarizing model on the whole *them* track.

The model returns text already split by speaker ("A", "B", ...). One request takes
at most 25 MB, so a long call is sent in pieces. To keep "A" in piece 2 the same
person as "A" in piece 1, a short clip of each speaker found so far is sent along
as a reference (the API accepts up to four).

OpenAI has announced that gpt-4o-transcribe-diarize shuts down on 26 Feb 2027; the
model name is a setting so a successor can be dropped in.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Callable

import numpy as np

from ..audio import wav
from ..config import SAMPLE_RATE as SR

MAX_BYTES = 24 * 1024 * 1024  # the API limit is 25 MB per upload
PIECE_SECONDS = 20 * 60  # FLAC of 20 min of 16 kHz mono speech stays well under the limit
MAX_KNOWN = 4  # reference speakers the API accepts
CLIP_SECONDS = (2.0, 8.0)  # reference clips must be 2-10 s


def flac(pcm: np.ndarray) -> bytes:
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, pcm, SR, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


def quiet_point(pcm: np.ndarray, target: int, search: int = 60 * SR) -> int:
    """The quietest half second near `target`, so a piece does not end mid-word."""
    lo, hi = max(0, target - search), min(len(pcm), target + search)
    block = SR // 2
    best, best_energy = target, None
    for a in range(lo, hi - block, block // 2):
        energy = float(np.abs(pcm[a : a + block].astype(np.int32)).mean())
        if best_energy is None or energy < best_energy:
            best, best_energy = a + block // 2, energy
    return min(max(best, 1), len(pcm))


def split_points(pcm: np.ndarray, piece: int) -> list[tuple[int, int]]:
    out, start = [], 0
    while len(pcm) - start > piece * 1.2:
        cut = quiet_point(pcm, start + piece)
        if cut <= start:
            cut = start + piece
        out.append((start, cut))
        start = cut
    out.append((start, len(pcm)))
    return out


def _field(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def transcribe_track(
    client,
    model: str,
    wav_path: Path,
    progress: Callable[[float], None] | None = None,
    piece_seconds: float = PIECE_SECONDS,
) -> list[dict]:
    """Speaker-labelled segments for the whole track: [{start, end, speaker, text}], times in seconds."""
    pcm = wav.read(wav_path)
    if len(pcm) < SR // 2:
        return []
    pieces = split_points(pcm, int(piece_seconds * SR))
    known: dict[str, str] = {}  # our speaker name -> reference clip (data URL)
    segments: list[dict] = []
    for n, (a, b) in enumerate(pieces):
        audio = pcm[a:b]
        data = flac(audio)
        if len(data) > MAX_BYTES:
            raise RuntimeError("A piece of the recording is too large to upload. Lower the piece length.")
        extra = {}
        if known:
            extra = {"extra_body": {"known_speaker_names": list(known), "known_speaker_references": list(known.values())}}
        r = client.audio.transcriptions.create(
            model=model, file=(f"them-{n + 1}.flac", data, "audio/flac"),
            response_format="diarized_json", chunking_strategy="auto", **extra,
        )
        rename: dict[str, str] = {}
        piece_segments = []
        for seg in _field(r, "segments", []) or []:
            text = (_field(seg, "text", "") or "").strip()
            label = str(_field(seg, "speaker", "") or "?")
            if not text:
                continue
            if label not in known:  # a voice the model did not match to a reference: a new speaker
                rename.setdefault(label, f"spk{len(known) + len(rename) + 1}")
            start, end = float(_field(seg, "start", 0.0)), float(_field(seg, "end", 0.0))
            piece_segments.append({
                "start": a / SR + start, "end": a / SR + max(end, start + 0.05),
                "speaker": rename.get(label, label), "text": text,
            })
        segments += piece_segments
        # give later pieces a clip of each new speaker
        for name in rename.values():
            if len(known) >= MAX_KNOWN:
                break
            clip = _reference_clip(pcm, [s for s in piece_segments if s["speaker"] == name])
            if clip:
                known[name] = clip
        if progress:
            progress((n + 1) / len(pieces))
    segments.sort(key=lambda s: s["start"])
    return segments


def _reference_clip(pcm: np.ndarray, segments: list[dict]) -> str | None:
    """The speaker's longest stretch, trimmed to what the API accepts, as a WAV data URL."""
    if not segments:
        return None
    best = max(segments, key=lambda s: s["end"] - s["start"])
    length = best["end"] - best["start"]
    if length < CLIP_SECONDS[0]:
        return None
    length = min(length, CLIP_SECONDS[1])
    mid = (best["start"] + best["end"]) / 2
    a = int((mid - length / 2) * SR)
    clip = pcm[max(0, a) : max(0, a) + int(length * SR)]
    return "data:audio/wav;base64," + base64.b64encode(wav.encode(clip)).decode()


def exclusive_turns(segments: list[dict]) -> list[dict]:
    """Speaker turns without overlaps, from the model's segments."""
    turns: list[dict] = []
    for s in sorted(segments, key=lambda s: s["start"]):
        start, end = s["start"], s["end"]
        if turns and start < turns[-1]["end"]:
            if turns[-1]["speaker"] == s["speaker"]:
                turns[-1]["end"] = max(turns[-1]["end"], end)
                continue
            turns[-1]["end"] = max(turns[-1]["start"] + 0.01, start)
        turns.append({"start": round(start, 3), "end": round(end, 3), "speaker": s["speaker"]})
    return turns
