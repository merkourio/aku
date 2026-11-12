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


def build_speakers(diarization: dict, previous: dict | None = None, track: str = "system") -> dict:
    """Speakers of one track from a diarization result: ids by first appearance, clips, suggested names.

    The other side gets S1, S2, ...; people at the microphone (interview mode) get M1, M2, ...,
    and one of them is marked as the owner ("me"): the one the user picked, else the voice match.
    """
    summary = speaker_summary(diarization.get("exclusive") or diarization.get("turns") or [])
    embeddings = {s["label"]: s.get("embedding") for s in diarization.get("speakers", [])}
    space = diarization.get("space", "")
    suggestions = voiceprints.suggest(
        [{"label": lab, "embedding": embeddings.get(lab)} for lab in summary], space
    )
    carried = _carry_names(diarization, previous) if previous else {}
    me, me_by = None, None
    if track == "mic":
        me = next((label for label, old in carried.items() if old.get("me") and label in summary), None)
        me_by = "user" if me else None
        if me is None:
            match = voiceprints.pick_me([{"label": lab, "embedding": embeddings.get(lab)} for lab in summary], space)
            if match:
                me, me_by = match[0], "match"
    prefix = "M" if track == "mic" else "S"
    speakers = {}
    for n, (label, info) in enumerate(sorted(summary.items(), key=lambda kv: kv[1]["first"]), start=1):
        suggested = suggestions.get(label) if label != me else None
        old = carried.get(label, {})
        speakers[f"{prefix}{n}"] = {
            "label": label,
            "track": track,
            "space": space,
            "me": label == me,
            "me_by": me_by if label == me else None,
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
    if sid.startswith("M"):  # someone at the microphone who is not the owner (interview mode)
        return f"Guest {sid[1:]}"
    return f"Speaker {sid[1:]}" if sid.startswith("S") else sid


def build_lines(
    segments: list[Segment], diarization: dict | None, speakers: dict, rules=None, mic_diarization: dict | None = None
) -> list[dict]:
    """mic_diarization: speaker turns on the microphone track (interview mode); without it the mic is the owner."""
    compiled = vocab.compile_rules() if rules is None else rules
    label_to_id: dict[str, dict[str, str]] = {"system": {}, "mic": {}}  # labels repeat across tracks
    for sid, s in speakers.get("speakers", {}).items():
        label_to_id[s.get("track", "system")][s["label"]] = ME if s.get("me") else sid
    turns = {
        track: Turns(d.get("exclusive") or []) if d else None
        for track, d in (("system", diarization), ("mic", mic_diarization))
    }
    raw: list[dict] = []
    for seg in segments:
        track = "mic" if seg.track == "mic" else "system"
        unknown = ME if track == "mic" else THEM
        t = turns[track]
        if t is None or not t.turns or not seg.words:
            raw.append({"start": seg.start, "end": seg.end, "speaker": unknown, "text": seg.text})
        else:
            for label, words in runs(seg.words, assign_words(seg.words, t)):
                raw.append({
                    "start": words[0].start,
                    "end": words[-1].end,
                    "speaker": label_to_id[track].get(label, unknown),
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
