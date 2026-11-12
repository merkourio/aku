"""Saved voices: speaker embeddings stored locally, matched by cosine similarity."""

from __future__ import annotations

import numpy as np

from .. import db

MATCH = 0.55  # cosine similarity needed to suggest a name
KEEP_PER_NAME = 8


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    return v / max(float(np.linalg.norm(v)), 1e-9)


def add(name: str, embedding: list[float], space: str, session: str | None = None) -> None:
    name = " ".join(name.split())
    if not name or embedding is None:
        return
    with db.connect() as con:
        con.execute(
            "INSERT INTO voiceprints (name, space, embedding, session) VALUES (?, ?, ?, ?)",
            (name, space, _unit(embedding).tobytes(), session),
        )
        # keep the most recent samples per person
        con.execute(
            """DELETE FROM voiceprints WHERE name = ? AND space = ? AND id NOT IN (
                   SELECT id FROM voiceprints WHERE name = ? AND space = ? ORDER BY id DESC LIMIT ?)""",
            (name, space, name, space, KEEP_PER_NAME),
        )


def people() -> list[dict]:
    with db.connect() as con:
        rows = con.execute(
            "SELECT name, COUNT(*) AS samples, MAX(created) AS updated FROM voiceprints GROUP BY name ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [dict(r) for r in rows]


def names() -> list[str]:
    return [p["name"] for p in people()]


def forget(name: str) -> int:
    with db.connect() as con:
        return con.execute("DELETE FROM voiceprints WHERE name = ?", (name,)).rowcount


def match(embedding: list[float] | None, space: str) -> tuple[str, float] | None:
    """Best saved voice for this embedding, if it is close enough."""
    if embedding is None:
        return None
    with db.connect() as con:
        rows = con.execute("SELECT name, embedding FROM voiceprints WHERE space = ?", (space,)).fetchall()
    if not rows:
        return None
    v = _unit(embedding)
    by_name: dict[str, list[np.ndarray]] = {}
    for r in rows:
        e = np.frombuffer(r["embedding"], dtype=np.float32)
        if e.shape == v.shape:
            by_name.setdefault(r["name"], []).append(e)
    best = None
    for name, samples in by_name.items():
        score = float(_unit(np.mean(samples, axis=0)) @ v)
        if best is None or score > best[1]:
            best = (name, score)
    return best if best and best[1] >= MATCH else None


def suggest(speakers: list[dict], space: str) -> dict[str, tuple[str, float]]:
    """One name per speaker at most, and each saved voice used once (best score wins)."""
    candidates = []
    for s in speakers:
        m = match(s.get("embedding"), space)
        if m:
            candidates.append((m[1], s["label"], m[0]))
    out: dict[str, tuple[str, float]] = {}
    used: set[str] = set()
    for score, label, name in sorted(candidates, reverse=True):
        if name not in used:
            out[label] = (name, round(score, 2))
            used.add(name)
    return out


# --- the owner's own voice (interview mode: who at the microphone is me) -----------------


def add_me(embedding: list[float] | None, space: str, session: str | None = None, replace: bool = False) -> None:
    """replace: a fresh enrolment starts over; otherwise the sample joins the recent ones."""
    if embedding is None:
        return
    with db.connect() as con:
        if replace:
            con.execute("DELETE FROM my_voice WHERE space = ?", (space,))
        con.execute("INSERT INTO my_voice (space, embedding, session) VALUES (?, ?, ?)", (space, _unit(embedding).tobytes(), session))
        con.execute(
            "DELETE FROM my_voice WHERE space = ? AND id NOT IN (SELECT id FROM my_voice WHERE space = ? ORDER BY id DESC LIMIT ?)",
            (space, space, KEEP_PER_NAME),
        )


def me_vector(space: str) -> np.ndarray | None:
    with db.connect() as con:
        rows = con.execute("SELECT embedding FROM my_voice WHERE space = ?", (space,)).fetchall()
    samples = [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
    samples = [s for s in samples if s.shape == samples[-1].shape] if samples else []
    return _unit(np.mean(samples, axis=0)) if samples else None


def me_info() -> list[dict]:
    """Per speaker-separation method the owner's voice is enrolled for."""
    with db.connect() as con:
        rows = con.execute("SELECT space, COUNT(*) AS samples, MAX(created) AS updated FROM my_voice GROUP BY space ORDER BY space").fetchall()
    return [dict(r) for r in rows]


def forget_me() -> int:
    with db.connect() as con:
        return con.execute("DELETE FROM my_voice").rowcount


def pick_me(speakers: list[dict], space: str) -> tuple[str, float] | None:
    """The speaker that sounds most like the owner, if any is close enough: (label, score)."""
    mine = me_vector(space)
    if mine is None:
        return None
    best = None
    for s in speakers:
        if s.get("embedding") is None:
            continue
        v = _unit(s["embedding"])
        if v.shape != mine.shape:
            continue
        score = float(v @ mine)
        if best is None or score > best[1]:
            best = (s["label"], score)
    return (best[0], round(best[1], 2)) if best and best[1] >= MATCH else None
