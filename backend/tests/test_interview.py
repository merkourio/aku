"""Interview mode: several people at the microphone, one of them the owner."""

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aku.api.server import create_app
from aku.audio import wav
from aku.diarize import voiceprints
from aku.diarize.live import SpeakerTracker
from aku.sessions import merge
from aku.sessions.liveview import LiveView
from aku.sessions.manager import Invalid, Manager, normalise_sources
from aku.sessions.store import Session, new_session

from test_live import VOICES, feed, speakers_at
from test_live import seg as live_seg
from test_manager import make_session
from test_transcript import DIAR, seg

# two people at the microphone: the first talks, then the second, then the first again
MIC_DIAR = {
    "engine": "basic", "space": "test",
    "exclusive": [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
        {"start": 2.0, "end": 4.0, "speaker": "SPEAKER_01"},
        {"start": 10.0, "end": 12.0, "speaker": "SPEAKER_00"},
    ],
    "speakers": [{"label": "SPEAKER_00", "embedding": [1.0, 0.0]}, {"label": "SPEAKER_01", "embedding": [0.0, 1.0]}],
}


def interview_session(with_system: bool = False) -> Session:
    """An interview right after processing: nobody is known to be the owner yet."""
    s = new_session()
    s.update(id=s.id, created="2026-11-12T10:00:00+01:00", status="naming", title="", duration=12.0, backend="local",
             backend_name="fake", vocab_sets=[], language="en", user_name="Taylor", interview=True,
             sources={"input": None, "output": None, "scope": "all" if with_system else "none", "apps": []})
    segments = [seg("mic", 0.0, "one two three four five six seven eight nine ten"), seg("mic", 10.0, "back again")]
    if with_system:
        segments.append(seg("system", 5.0, "hello from the call"))
    for i, sg in enumerate(segments, start=1):
        sg.id = i
        s.append_segment(sg)
    for path in (s.mic, s.system):
        w = wav.WavWriter(path)
        w.write(np.zeros(16000 * 12, dtype=np.int16))
        w.close()
    s.mic_diarization_path.write_text(json.dumps(MIC_DIAR))
    speakers = merge.build_speakers(MIC_DIAR, track="mic")
    if with_system:
        system = {"engine": "basic", "space": "test", "exclusive": [{"start": 5.0, "end": 7.0, "speaker": "SPEAKER_00"}],
                  "speakers": [{"label": "SPEAKER_00", "embedding": [0.6, 0.8]}]}
        s.diarization_path.write_text(json.dumps(system))
        speakers["speakers"] = {**merge.build_speakers(system)["speakers"], **speakers["speakers"]}
    s.save_speakers(speakers)
    s.save_transcript({"lines": merge.build_lines(s.segments(), s.diarization, s.speakers, mic_diarization=MIC_DIAR)})
    return s


def test_microphone_lines_are_split_by_speaker():
    speakers = merge.build_speakers(MIC_DIAR, track="mic")
    assert list(speakers["speakers"]) == ["M1", "M2"]
    assert not any(s["me"] for s in speakers["speakers"].values())  # no enrolled voice: nobody is the owner yet
    segments = [seg("mic", 0.0, "one two three four five six seven eight nine ten"), seg("mic", 10.0, "back again")]
    lines = merge.build_lines(segments, None, speakers, mic_diarization=MIC_DIAR)
    assert [(l["speaker"], l["text"]) for l in lines] == [
        ("M1", "one two three four five"), ("M2", "six seven eight nine ten"), ("M1", "back again")]
    assert merge.display_name("M2", speakers, "Taylor") == "Guest 2"


def test_enrolled_voice_marks_the_owner():
    voiceprints.add_me([0.1, 1.0], "test")
    speakers = merge.build_speakers(MIC_DIAR, track="mic")["speakers"]
    assert [(k, s["me"], s["me_by"]) for k, s in speakers.items()] == [("M1", False, None), ("M2", True, "match")]
    segments = [seg("mic", 0.0, "one two three four five six seven eight nine ten")]
    lines = merge.build_lines(segments, None, {"speakers": speakers}, mic_diarization=MIC_DIAR)
    assert [l["speaker"] for l in lines] == ["M1", "me"]
    # a voice in another method's space says nothing about this one
    voiceprints.forget_me()
    voiceprints.add_me([0.1, 1.0], "other-space")
    assert not any(s["me"] for s in merge.build_speakers(MIC_DIAR, track="mic")["speakers"].values())


def test_the_same_labels_on_both_tracks_do_not_mix():
    s = interview_session(with_system=True)
    assert list(s.speakers["speakers"]) == ["S1", "M1", "M2"]
    lines = merge.build_lines(s.segments(), s.diarization, s.speakers, mic_diarization=MIC_DIAR)
    assert [l["speaker"] for l in lines] == ["M1", "M2", "S1", "M1"]


def test_picking_myself_relabels_the_lines_and_teaches_my_voice():
    m = Manager()
    s = interview_session()
    d = m.detail(s.id)
    assert d["interview"] and d["system_off"]
    assert [(x["id"], x["track"], x["me"], x["display"]) for x in d["speakers"]] == [
        ("M1", "mic", False, "Guest 1"), ("M2", "mic", False, "Guest 2")]
    assert [l["name"] for l in d["lines"]] == ["Guest 1", "Guest 2", "Guest 1"]

    d = m.set_speaker(s.id, "M1", None, me=True)
    assert [l["name"] for l in d["lines"]] == ["Taylor", "Guest 2", "Taylor"]
    assert [(x["me"], x["display"], x["lines"]) for x in d["speakers"]] == [(True, "Taylor", 2), (False, "Guest 2", 1)]
    d = m.set_speaker(s.id, "M2", None, me=True)  # changed my mind: only one speaker can be me
    assert [x["me"] for x in d["speakers"]] == [False, True]
    d = m.set_speaker(s.id, "M2", None, me=False)
    assert not any(x["me"] for x in d["speakers"])
    m.set_speaker(s.id, "M1", None, me=True)
    m.set_speaker(s.id, "M2", "Anna", remember=True)

    assert voiceprints.me_info() == []  # nothing is kept before finalising
    final = m.finalize(s.id, title="Interview")
    assert "Taylor:** one two three four five" in open(final["output_path"]).read()
    assert "Anna:** six seven" in open(final["output_path"]).read()
    assert [i["space"] for i in voiceprints.me_info()] == ["test"]
    assert voiceprints.names() == ["Anna"]  # the owner is not one of the saved people
    # next interview: recognised without asking
    assert merge.build_speakers(MIC_DIAR, track="mic")["speakers"]["M1"]["me_by"] == "match"
    assert voiceprints.forget_me() == 1


def test_my_own_pick_survives_a_rerun(monkeypatch):
    m = Manager()
    s = interview_session(with_system=True)
    m.set_speaker(s.id, "M2", None, me=True)
    m.set_speaker(s.id, "S1", "Bo")
    swapped = json.loads(json.dumps(MIC_DIAR).replace("SPEAKER_00", "X").replace("SPEAKER_01", "SPEAKER_00").replace("X", "SPEAKER_01"))
    ran = []

    def fake_run(wav_path, out_path, speakers, engine, progress=None):
        ran.append((wav_path.name, speakers))
        out_path.write_text(json.dumps(swapped))
        return swapped

    monkeypatch.setattr("aku.diarize.run", fake_run)
    m.rediarize(s.id, 2, track="mic")
    m.wait(s.id)
    assert ran == [("mic.wav", 2)]  # the other side is left alone
    d = m.detail(s.id)
    assert d["mic_speakers_requested"] == 2 and d["speakers_requested"] == "auto"
    assert [(x["id"], x["me"], x["name"]) for x in d["speakers"]] == [("S1", False, "Bo"), ("M1", False, None), ("M2", True, None)]
    assert [l["name"] for l in d["lines"]] == ["Guest 1", "Taylor", "Bo", "Guest 1"]


def test_a_normal_call_is_unaffected():
    m = Manager()
    voiceprints.add_me([1.0, 0.0], "test")  # an enrolled voice must not touch the other side of a call
    s = make_session()
    s.save_speakers(merge.build_speakers(DIAR))
    d = m.detail(s.id)
    assert not d["interview"] and not d["system_off"]
    assert all(x["track"] == "system" and not x["me"] for x in d["speakers"])
    assert [l["name"] for l in d["lines"]] == ["Speaker 1", "Speaker 2", "Taylor", "Speaker 1"]
    with pytest.raises(Invalid):
        m.rediarize(s.id, 2, track="mic")
    with pytest.raises(Invalid):
        m.set_speaker(s.id, "S1", None, me=True)
    with pytest.raises(Invalid):
        normalise_sources({"scope": "none"})
    assert normalise_sources({"scope": "none"}, interview=True)["scope"] == "none"


def test_live_view_labels_people_at_the_microphone():
    view = LiveView("Taylor", [])
    first = view.add(live_seg("mic", 1, 0.0, "hello there yes indeed"))
    assert first[0]["name"] == "Taylor"  # until the voices are told apart
    them = view.add(live_seg("system", 2, 0.0, "from the call"))
    changed = view.relabel([{"start": 0.0, "end": 2.0, "speaker": "me"}, {"start": 2.0, "end": 4.0, "speaker": "M2"}], "mic")
    assert list(changed) == [1]
    assert [(l["name"], l["text"]) for l in changed[1]] == [("Taylor", "hello there"), ("Guest 2", "yes indeed")]
    assert view.all()[0]["name"] in ("Taylor", "Them") and them[0]["name"] == "Them"


def test_live_tracker_finds_the_owner_among_the_voices():
    owner = (VOICES["b"] / np.linalg.norm(VOICES["b"])).astype(np.float32)
    t = SpeakerTracker(prefix="M", owner=owner)
    feed(t, "a", 0)
    assert speakers_at(t.update(None), 1.0) == ["M1"]
    feed(t, "b", 5)
    feed(t, "a", 10)
    assert speakers_at(t.update(None), 1.0, 6.0, 11.0) == ["M1", "me", "M1"]
    assert t.found == 2


def test_api_carries_the_interview_fields(monkeypatch):
    m = Manager()
    s = interview_session()
    with TestClient(create_app(m, port=4587), base_url="http://127.0.0.1:4587") as c:
        r = c.put(f"/sessions/{s.id}/speakers/M2", json={"me": True})
        assert r.status_code == 200 and [x["me"] for x in r.json()["speakers"]] == [False, True]
        assert c.get("/voiceprints/me").json() == []
        voiceprints.add_me([1.0, 0.0], "test")
        assert c.get("/settings").json()["my_voice"][0]["space"] == "test"
        assert c.delete("/voiceprints/me").json() == {"forgotten": 1}
        assert c.post(f"/sessions/{s.id}/diarize", json={"speakers": 2, "track": "nope"}).status_code == 400
        status = c.get("/status").json()
        assert status["interview"] is False and status["enrolling"] is False
