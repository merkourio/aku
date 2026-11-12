"""The transcript as shown during a call.

Each transcribed segment becomes one line, or several once live speaker separation
knows who spoke when. Earlier lines are relabelled as the picture gets clearer
(a second voice appears, or the user changes the speaker count).
"""

from __future__ import annotations

import threading

from ..asr.live import Segment
from ..diarize.assign import Turns, assign_words, runs
from ..vocab import store as vocab
from . import merge


class LiveView:
    def __init__(self, user_name: str, rules: list):
        self.user_name = user_name
        self.rules = rules
        self.turns: dict[str, Turns] = {}  # per track; the mic only has turns in interview mode
        self._segments: dict[int, Segment] = {}
        self._lines: dict[int, list[dict]] = {}
        self._lock = threading.Lock()

    def _build(self, seg: Segment) -> list[dict]:
        unknown = merge.ME if seg.track == "mic" else merge.THEM
        turns = self.turns.get(seg.track)
        if turns is None or not turns.turns or not seg.words:
            parts = [(unknown, seg.start, seg.end, seg.text)]
        else:
            parts = [
                (label or unknown, words[0].start, words[-1].end, " ".join(w.text for w in words))
                for label, words in runs(seg.words, assign_words(seg.words, turns))
            ]
        return [
            {
                "id": round(seg.id + i / 100, 2),
                "segment": seg.id,
                "start": round(start, 2),
                "end": round(end, 2),
                "speaker": speaker,
                "name": merge.display_name(speaker, {}, self.user_name),
                "text": vocab.apply_rules(text, self.rules),
            }
            for i, (speaker, start, end, text) in enumerate(parts)
        ]

    def add(self, seg: Segment) -> list[dict]:
        with self._lock:
            self._segments[seg.id] = seg
            self._lines[seg.id] = lines = self._build(seg)
            return lines

    def relabel(self, turns: list[dict], track: str = "system") -> dict[int, list[dict]]:
        """New speaker turns arrived for a track: returns the segments whose lines changed."""
        with self._lock:
            self.turns[track] = Turns(turns)
            changed = {}
            for sid, seg in self._segments.items():
                if seg.track != track:
                    continue
                lines = self._build(seg)
                if lines != self._lines[sid]:
                    self._lines[sid] = changed[sid] = lines
            return changed

    def refresh(self, rules: list) -> None:
        """Replacement rules changed mid-call."""
        with self._lock:
            self.rules = rules
            for sid, seg in self._segments.items():
                self._lines[sid] = self._build(seg)

    def all(self) -> list[dict]:
        with self._lock:
            lines = [line for group in self._lines.values() for line in group]
        return sorted(lines, key=lambda l: (l["start"], l["id"]))
