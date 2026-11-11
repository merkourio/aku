"""Map words to speakers, and pick a representative clip per speaker."""

from __future__ import annotations

import bisect

from ..asr.base import Word

NEAR = 1.0  # a word outside every turn takes the nearest turn within this many seconds


class Turns:
    """Non-overlapping speaker turns, searchable by time."""

    def __init__(self, turns: list[dict]):
        self.turns = sorted(turns, key=lambda t: t["start"])
        self.starts = [t["start"] for t in self.turns]

    def speaker_at(self, t: float) -> str | None:
        i = bisect.bisect_right(self.starts, t) - 1
        if i >= 0 and self.turns[i]["start"] <= t < self.turns[i]["end"]:
            return self.turns[i]["speaker"]
        best, best_d = None, NEAR
        for j in (i, i + 1):
            if 0 <= j < len(self.turns):
                turn = self.turns[j]
                d = min(abs(t - turn["start"]), abs(t - turn["end"]))
                if d < best_d:
                    best, best_d = turn["speaker"], d
        return best


def assign_words(words: list[Word], turns: Turns) -> list[str | None]:
    """A speaker label per word: whoever is talking at the word's midpoint."""
    labels = [turns.speaker_at((w.start + w.end) / 2) for w in words]
    # words outside any turn follow their neighbours
    last = None
    for i, lab in enumerate(labels):
        if lab is None:
            labels[i] = last
        else:
            last = lab
    nxt = None
    for i in range(len(labels) - 1, -1, -1):
        if labels[i] is None:
            labels[i] = nxt
        else:
            nxt = labels[i]
    # a single short word between two words of another speaker is a timing wobble, not a turn
    for i in range(1, len(labels) - 1):
        if labels[i - 1] == labels[i + 1] != labels[i] and words[i].end - words[i].start < 0.4:
            labels[i] = labels[i - 1]
    return labels


def runs(words: list[Word], labels: list[str | None]) -> list[tuple[str | None, list[Word]]]:
    """Consecutive words by the same speaker."""
    out: list[tuple[str | None, list[Word]]] = []
    for w, lab in zip(words, labels):
        if out and out[-1][0] == lab:
            out[-1][1].append(w)
        else:
            out.append((lab, [w]))
    return out


def speaker_summary(exclusive: list[dict]) -> dict[str, dict]:
    """Per label: total talk time, first appearance and a clip to play in the naming screen."""
    info: dict[str, dict] = {}
    for t in sorted(exclusive, key=lambda t: t["start"]):
        d = t["end"] - t["start"]
        s = info.setdefault(t["speaker"], {"duration": 0.0, "first": t["start"], "best": None})
        s["duration"] += d
        # the clip: the longest stretch, capped; ties go to the earliest
        if s["best"] is None or d > s["best"][1] - s["best"][0] + 0.5:
            s["best"] = (t["start"], t["end"])
    for s in info.values():
        a, b = s.pop("best")
        if b - a > 8.0:
            mid = (a + b) / 2
            a, b = mid - 4.0, mid + 4.0
        s["clip"] = [round(max(0.0, a - 0.15), 2), round(b + 0.15, 2)]
        s["duration"] = round(s["duration"], 1)
    return info
