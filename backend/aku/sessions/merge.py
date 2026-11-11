"""Build the speaker-labelled transcript from raw segments, diarization and names."""

from __future__ import annotations

from ..asr.live import Segment
from ..diarize import voiceprints
from ..diarize.assign import Turns, assign_words, runs, speaker_summary
from ..vocab import store as vocab

ME = "me"
THEM = "them"  # the other side before (or without) diarization
JOIN_GAP = 1.5  # seconds: same speaker carries on -> same paragraph
JOIN_CHARS = 500


def build_speakers(diarization: dict, previous: dict | None = None) -> dict:
    """speakers.json from a diarization result: ids S1.. by first appearance, clips, suggested names."""
    summary = speaker_summary(diarization.get("exclusive") or diarization.get("turns") or [])
    embeddings = {s["label"]: s.get("embedding") for s in diarization.get("speakers", [])}
    space = diarization.get("space", "")
    suggestions = voiceprints.suggest(
        [{"label": lab, "embedding": embeddings.get(lab)} for lab in summary], space
    )
    carried = _carry_names(diarization, previous) if previous else {}
    speakers = {}
    for n, (label, info) in enumerate(sorted(summary.items(), key=lambda kv: kv[1]["first"]), start=1):
        suggested = suggestions.get(label)
        old = carried.get(label, {})
        speakers[f"S{n}"] = {
            "label": label,
            "name": old.get("name"),
            "suggested": suggested[0] if suggested else None,
            "score": suggested[1] if suggested else None,
            "duration": info["duration"],
            "clip": info["clip"],
            "remember": bool(old.get("remember", False)),
            "saved": False,
        }
    return {"engine": diarization.get("engine"), "space": space, "speakers": speakers}


def _carry_names(diarization: dict, previous: dict) -> dict[str, dict]:
    """After a re-run, keep names where a new speaker mostly covers an old one."""
    old_turns = previous.get("turns") or []
    named = previous.get("named") or {}  # old label -> {"name", "remember"}
    if not old_turns or not named:
        return {}
    overlap: dict[tuple[str, str], float] = {}
    total: dict[str, float] = {}
    for new in diarization.get("exclusive") or []:
        total[new["speaker"]] = total.get(new["speaker"], 0.0) + new["end"] - new["start"]
        for old in old_turns:
            o = min(new["end"], old["end"]) - max(new["start"], old["start"])
            if o > 0:
                key = (new["speaker"], old["speaker"])
                overlap[key] = overlap.get(key, 0.0) + o
    out, used = {}, set()
    for (new_label, old_label), o in sorted(overlap.items(), key=lambda kv: -kv[1]):
        if new_label in out or old_label in used or old_label not in named:
            continue
        if o >= 0.5 * total[new_label]:
            out[new_label] = named[old_label]
            used.add(old_label)
    return out


def display_name(sid: str, speakers: dict, user_name: str) -> str:
    if sid == ME:
        return user_name
    if sid == THEM:
        return "Them"
    s = speakers.get("speakers", {}).get(sid)
    if s and s.get("name"):
        return s["name"]
    return f"Speaker {sid[1:]}" if sid.startswith("S") else sid


def build_lines(segments: list[Segment], diarization: dict | None, speakers: dict, rules=None) -> list[dict]:
    compiled = vocab.compile_rules() if rules is None else rules
    label_to_id = {s["label"]: sid for sid, s in speakers.get("speakers", {}).items()}
    turns = Turns((diarization or {}).get("exclusive") or []) if diarization else None
    raw: list[dict] = []
    for seg in segments:
        if seg.track == "mic":
            raw.append({"start": seg.start, "end": seg.end, "speaker": ME, "text": seg.text})
        elif turns is None or not turns.turns or not seg.words:
            raw.append({"start": seg.start, "end": seg.end, "speaker": THEM, "text": seg.text})
        else:
            for label, words in runs(seg.words, assign_words(seg.words, turns)):
                raw.append({
                    "start": words[0].start,
                    "end": words[-1].end,
                    "speaker": label_to_id.get(label, THEM),
                    "text": " ".join(w.text for w in words),
                })
    raw.sort(key=lambda l: (l["start"], l["speaker"] != ME))
    lines: list[dict] = []
    for line in raw:
        last = lines[-1] if lines else None
        if (
            last
            and last["speaker"] == line["speaker"]
            and line["start"] - last["end"] <= JOIN_GAP
            and len(last["text"]) + len(line["text"]) <= JOIN_CHARS
        ):
            last["text"] += " " + line["text"]
            last["end"] = max(last["end"], line["end"])
        else:
            lines.append(dict(line))
    for i, line in enumerate(lines):
        line["id"] = i
        line["start"], line["end"] = round(line["start"], 2), round(line["end"], 2)
        line["text"] = vocab.apply_rules(line["text"], compiled)
    return lines


def participants(lines: list[dict], speakers: dict, user_name: str) -> list[str]:
    out: list[str] = []
    for line in lines:
        name = display_name(line["speaker"], speakers, user_name)
        if name not in out:
            out.append(name)
    return out
