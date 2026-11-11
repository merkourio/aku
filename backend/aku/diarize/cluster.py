"""The built-in speaker separation, minus the neural network: phrases, windows, clustering, turns.

Shared by the after-call pass (worker.py) and live separation during a call (live.py), so both
label speakers the same way. No torch here: embeddings come in as plain arrays.
"""

from __future__ import annotations

import numpy as np

from ..config import SAMPLE_RATE as SR

WIN = int(1.5 * SR)
HOP = int(0.5 * SR)
WHOLE = int(2.5 * SR)  # phrases up to this length are embedded whole
RELIABLE = int(1.0 * SR)  # shorter windows give noisy embeddings
THRESHOLD = 0.75  # cosine distance at which two voices count as different (automatic count)
MAX_FIT = 800  # cluster at most this many windows; the rest join the nearest voice

Window = tuple[int, int, int]  # (phrase index, start sample, end sample)


def find_phrases(pcm: np.ndarray, vad, offset: int = 0) -> list[tuple[int, int]]:
    """Speech stretches separated by short pauses (speaker changes usually sit on one).

    pcm is int16; vad is a SileroVAD. Returned sample positions have `offset` added.
    """
    from ..asr.vad import FRAME

    vad.reset()
    probs = [vad(pcm[i : i + FRAME]) for i in range(0, len(pcm) - FRAME + 1, FRAME)]
    out, start, quiet = [], None, 0
    min_gap = 5  # frames (~0.16 s)
    for i, p in enumerate(probs):
        if p >= 0.5:
            if start is None:
                start = i
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet >= min_gap:
                out.append((start * FRAME, (i - quiet + 1) * FRAME))
                start, quiet = None, 0
    if start is not None:
        out.append((start * FRAME, (len(probs) - quiet) * FRAME))
    return [(a + offset, b + offset) for a, b in out if b - a >= int(0.2 * SR)]


def make_windows(spans: list[tuple[int, int]], first_phrase: int = 0) -> list[Window]:
    """Short phrases whole, long phrases in overlapping 1.5 s windows."""
    windows: list[Window] = []
    for n, (a, b) in enumerate(spans):
        pi = first_phrase + n
        if b - a <= WHOLE:
            windows.append((pi, a, b))
        else:
            for s in range(a, b - WIN + 1, HOP):
                windows.append((pi, s, s + WIN))
            if (b - a - WIN) % HOP:
                windows.append((pi, b - WIN, b))
    return windows


def label_windows(vectors: np.ndarray, windows: list[Window], speakers: int | None = None) -> tuple[np.ndarray, dict[int, np.ndarray]]:
    """A cluster number per window (-1 = unusable) and each cluster's centroid."""
    from sklearn.cluster import AgglomerativeClustering

    n = len(windows)
    labels = np.zeros(n, dtype=int)
    if n == 0:
        return labels, {}
    vectors = np.array(vectors, dtype=np.float32, copy=True)
    good = np.all(np.isfinite(vectors), axis=1)
    vectors[~good] = 0
    unit = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)
    length = np.array([b - a for _, a, b in windows])
    reliable = good & (length >= RELIABLE)
    if reliable.sum() < 2:
        reliable = good.copy()
    ridx = np.flatnonzero(reliable)

    if len(ridx) >= 2:
        fit = ridx if len(ridx) <= MAX_FIT else ridx[np.linspace(0, len(ridx) - 1, MAX_FIT).astype(int)]
        if speakers:
            model = AgglomerativeClustering(n_clusters=min(speakers, len(fit)), metric="cosine", linkage="average")
        else:
            model = AgglomerativeClustering(n_clusters=None, distance_threshold=THRESHOLD, metric="cosine", linkage="average")
        fl = model.fit_predict(unit[fit])
        if not speakers:
            # fold tiny clusters (stray noises, laughter) into the nearest real one
            dur = {c: length[fit][fl == c].sum() for c in set(fl)}
            big = [c for c in dur if dur[c] >= max(3.0 * SR, 0.03 * length[fit].sum())] or [max(dur, key=dur.get)]
            cents = {c: unit[fit][fl == c].mean(axis=0) for c in big}
            for k, c in enumerate(fl):
                if c not in cents:
                    fl[k] = max(cents, key=lambda b: float(unit[fit][k] @ cents[b]))
        if len(fit) == len(ridx):
            labels[ridx] = fl
        else:  # a long call: the windows left out of the fit join the nearest voice
            ids = sorted(set(fl))
            cents = np.stack([unit[fit][fl == c].mean(axis=0) for c in ids])
            labels[ridx] = np.array(ids)[np.argmax(unit[ridx] @ cents.T, axis=1)]

    centroids: dict[int, np.ndarray] = {}
    if len(ridx):
        for c in sorted(set(labels[ridx])):
            v = unit[ridx][labels[ridx] == c].mean(axis=0)
            centroids[int(c)] = v / max(float(np.linalg.norm(v)), 1e-9)

    # Short windows give noisy embeddings. A short phrase right next to a longer one
    # ("...here? he asked.") almost always belongs to the same speaker: follow the neighbour.
    near_limit = int(1.0 * SR)
    for i in np.flatnonzero(~reliable):
        _, a, b = windows[i]
        pos = int(np.searchsorted(ridx, i))
        best, best_gap = None, near_limit
        for j in (ridx[pos - 1] if pos > 0 else None, ridx[pos] if pos < len(ridx) else None):
            if j is None:
                continue
            gap = max(windows[j][1] - b, a - windows[j][2], 0)
            if gap < best_gap:
                best, best_gap = j, gap
        if best is not None:
            labels[i] = labels[best]
        elif good[i] and centroids:
            labels[i] = max(centroids, key=lambda c: float(unit[i] @ centroids[c]))
        else:
            labels[i] = -1
    return labels, centroids


def turns_from(spans: list[tuple[int, int]], windows: list[Window], labels: np.ndarray) -> list[dict]:
    """Non-overlapping turns: every 0.1 s of a phrase takes the label of the window centred closest to it.

    Turn speakers are the integer labels; times are seconds.
    """
    step = int(0.1 * SR)
    by_phrase: dict[int, list[int]] = {}
    for i, (pi, _, _) in enumerate(windows):
        by_phrase.setdefault(pi, []).append(i)
    turns: list[dict] = []
    previous = None
    for pi, (a, b) in enumerate(spans):
        wins = [i for i in by_phrase.get(pi, []) if labels[i] >= 0]
        centres = np.array([(windows[i][1] + windows[i][2]) / 2 for i in wins]) if wins else None
        t = a
        while t < b:
            e = min(t + step, b)
            if centres is None:
                lab = previous if previous is not None else 0
            else:
                lab = int(labels[wins[int(np.argmin(np.abs(centres - (t + e) / 2)))]])
            if turns and turns[-1]["speaker"] == lab and abs(turns[-1]["end"] - t / SR) < 1e-6:
                turns[-1]["end"] = e / SR
            else:
                turns.append({"start": t / SR, "end": e / SR, "speaker": lab})
            previous = lab
            t = e
    return turns
