import json
from pathlib import Path

import numpy as np
import pytest

from aku import diarize
from aku.audio import wav
from aku.diarize import voiceprints
from aku.sessions import merge
from aku.sessions.manager import Busy, Invalid, Manager, NotFound, normalise_sources, parse_speakers
from aku.sessions.store import Session, new_session
from aku.vocab import store as vocab

from test_transcript import DIAR, seg


def make_session(status="naming", audio=True) -> Session:
    """A session as it looks right after processing."""
    s = new_session()
    s.update(id=s.id, created="2026-11-11T16:15:00+01:00", status=status, title="", duration=12.0,
             backend="local", backend_name="fake", vocab_sets=[], language="en", user_name="Taylor")
    for i, sg in enumerate([seg("system", 0.0, "hello from Collect one two three four five six"),
                            seg("mic", 5.0, "my reply"), seg("system", 10.0, "back again")], start=1):
        sg.id = i
        s.append_segment(sg)
    if audio:
        for path in (s.mic, s.system):
            w = wav.WavWriter(path)
            w.write(np.zeros(16000 * 12, dtype=np.int16))
            w.close()
    s.diarization_path.write_text(json.dumps(DIAR))
    s.save_speakers(merge.build_speakers(DIAR))
    s.save_transcript({"lines": merge.build_lines(s.segments(), DIAR, s.speakers)})
    return s


def test_naming_then_finalise_writes_markdown_to_the_folder(tmp_path):
    m = Manager()
    s = make_session()
    d = m.set_speaker(s.id, "S1", "  Alex Example ", remember=True)
    assert d["speakers"][0]["name"] == "Alex Example"
    assert [l["name"] for l in d["lines"]] == ["Alex Example", "Speaker 2", "Taylor", "Alex Example"]
    assert voiceprints.names() == []  # nothing is remembered before finalising

    out = tmp_path / "vault" / "calls"
    d = m.finalize(s.id, "Weekly sync", str(out))
    path = Path(d["output_path"])
    assert path.parent == out and path.name == "2026-11-11 1615 Weekly sync.md"
    assert "**[00:00] Alex Example:** hello from Collect one two" in path.read_text()
    assert d["status"] == "finalised" and not s.temp_md.exists()
    assert voiceprints.names() == ["Alex Example"]  # only the ticked speaker


def test_renaming_after_finalise_rewrites_the_file_and_keeps_the_old_version(tmp_path):
    m = Manager()
    s = make_session()
    m.finalize(s.id, "Call", str(tmp_path))
    first = Path(s.meta["output_path"]).read_text()
    m.set_speaker(s.id, "S2", "Bo")
    now = Path(s.meta["output_path"]).read_text()
    assert "Speaker 2:" in first and "Bo:" in now and "Speaker 2" not in now
    assert (s.dir / "export.prev.md").read_text() == first


def test_refinalise_with_new_title_moves_the_file(tmp_path):
    m = Manager()
    s = make_session()
    old = Path(m.finalize(s.id, "Draft", str(tmp_path))["output_path"])
    new = Path(m.finalize(s.id, "Final name", str(tmp_path))["output_path"])
    assert new.exists() and not old.exists() and new.name.endswith("Final name.md")


def test_fix_from_transcript_updates_this_transcript_and_future_ones():
    m = Manager()
    s = make_session()
    m.fix("Collect", "ExampleCo", None, s.id)
    assert "ExampleCo" in m.detail(s.id)["lines"][0]["text"]
    assert vocab.list_rules()[0]["wrong"] == "Collect"


def test_rerun_with_a_speaker_count_keeps_names(monkeypatch):
    m = Manager()
    s = make_session()
    m.set_speaker(s.id, "S1", "Anna")
    seen = {}

    def fake_run(wav_path, out_path, speakers=None, engine="auto", progress=None):
        seen["speakers"] = speakers
        result = dict(DIAR, exclusive=[dict(t, speaker="NEW_" + t["speaker"][-1]) for t in DIAR["exclusive"]], speakers=[])
        Path(out_path).write_text(json.dumps(result))
        return result

    monkeypatch.setattr(diarize, "run", fake_run)
    m.rediarize(s.id, 2)
    m.wait(s.id)
    d = m.detail(s.id)
    assert seen["speakers"] == 2 and d["speakers_requested"] == 2
    assert d["speakers"][0]["name"] == "Anna"


def test_delete_audio_keeps_transcript_and_blocks_audio_features():
    m = Manager()
    s = make_session()
    assert len(m.speaker_clip(s.id, "S1")) > 1000
    d = m.delete_audio(s.id)
    assert d["has_audio"] is False and len(m.detail(s.id)["lines"]) == 4
    with pytest.raises(Invalid):
        m.improve(s.id, "local_large")
    with pytest.raises(Invalid):
        m.rediarize(s.id, "auto")
    with pytest.raises(NotFound):
        m.speaker_clip(s.id, "S1")


def test_unfinished_sessions_are_flagged_on_launch_and_show_their_saved_lines():
    s = make_session(status="recording")
    s.transcript_path.unlink()
    m = Manager()  # a fresh launch
    d = m.detail(s.id)
    assert d["status"] == "interrupted"
    assert [l["name"] for l in d["lines"]] == ["Them", "Taylor", "Them"]


def test_improve_remaps_names_onto_new_text_and_keeps_a_backup(monkeypatch):
    m = Manager()
    s = make_session()
    m.set_speaker(s.id, "S1", "Anna")

    class Better:
        name, model = "fake:better", "better"
        def start(self): pass
        def close(self): pass

    def fake_transcribe_file(path, track, backend, on_segment, prompt="", language="en", **kw):
        text = "hello from ExampleCo one two three four five six" if track == "system" else "my better reply"
        on_segment(seg(track, 0.0 if track == "system" else 5.0, text))
        return 1

    monkeypatch.setattr(m, "make_backend", lambda *a, **k: Better())
    monkeypatch.setattr("aku.sessions.manager.transcribe_file", fake_transcribe_file)
    m.improve(s.id, "local_large")
    m.wait(s.id)
    d = m.detail(s.id)
    assert d["improved_with"] == "fake:better"
    assert [(l["name"], l["text"]) for l in d["lines"]] == [
        ("Anna", "hello from ExampleCo one two"), ("Speaker 2", "three four five six"), ("Taylor", "my better reply")]
    assert (s.dir / "transcript.prev.json").exists() and (s.dir / "segments.prev.jsonl").exists()


def test_finalise_refuses_unready_sessions_and_relative_folders():
    m = Manager()
    with pytest.raises(Invalid):
        m.finalize(make_session(status="processing").id, "x")
    with pytest.raises(Invalid):
        m.finalize(make_session().id, "x", "relative/folder")
    with pytest.raises(NotFound):
        m.detail("../etc")


def test_speaker_count_and_sources_validation():
    assert parse_speakers("auto") is None and parse_speakers(None) is None and parse_speakers("3") == 3
    for bad in ("many", 0.5, 99, -1):
        with pytest.raises(Invalid):
            parse_speakers(bad)
    assert normalise_sources(None)["scope"] == "all"
    with pytest.raises(Invalid):
        normalise_sources({"scope": "apps", "apps": []})
    assert normalise_sources({"scope": "apps", "apps": ["teams-for-linux"]})["apps"] == ["teams-for-linux"]


def test_improve_with_cloud_speaker_labels_carries_names_over(monkeypatch):
    m = Manager()
    s = make_session()
    m.set_speaker(s.id, "S2", "Bo")  # S2 spoke 2-4 s in the local separation

    class Cloud:
        name, model, client = "openai:whisper-1", "whisper-1", object()
        def close(self): pass

    def fake_transcribe_file(path, track, backend, on_segment, prompt=None, language="en", **kw):
        assert track == "mic"  # the other side is not sent in chunks
        on_segment(seg("mic", 5.0, "my reply"))
        return 1

    def fake_track(client, model, wav_path, progress=None, **kw):
        assert model == "gpt-4o-transcribe-diarize" and wav_path == s.system
        return [
            {"start": 0.0, "end": 2.0, "speaker": "spk1", "text": "hello from ExampleCo"},
            {"start": 2.0, "end": 4.0, "speaker": "spk2", "text": "three four five six"},
            {"start": 10.0, "end": 11.0, "speaker": "spk1", "text": "back again"},
        ]

    monkeypatch.setattr(m, "make_backend", lambda *a, **k: Cloud())
    monkeypatch.setattr("aku.sessions.manager.transcribe_file", fake_transcribe_file)
    monkeypatch.setattr("aku.sessions.manager.cloud_diarize.transcribe_track", fake_track)
    monkeypatch.setattr(m, "_voice_embeddings", lambda path, turns: {t["speaker"]: [0.0, 1.0] for t in turns})
    m.improve(s.id, "cloud_diarize")
    m.wait(s.id)
    d = m.detail(s.id)
    assert d["error"] is None and d["improved_with"] == "openai:whisper-1 + gpt-4o-transcribe-diarize"
    assert d["diarizer"] == "openai"
    assert [(l["name"], l["text"]) for l in d["lines"]] == [
        ("Speaker 1", "hello from ExampleCo"), ("Bo", "three four five six"), ("Taylor", "my reply"), ("Speaker 1", "back again")]
    assert (s.dir / "diarization.prev.json").exists() and (s.dir / "transcript.prev.json").exists()
