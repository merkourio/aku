from pathlib import Path

from aku.asr.base import Word
from aku.asr.live import Segment
from aku.diarize import voiceprints
from aku.diarize.assign import Turns, assign_words, runs, speaker_summary
from aku.sessions import markdown, merge
from aku.vocab import store as vocab


def seg(track, start, words, sid=1):
    ws = [Word(start + i * 0.4, start + i * 0.4 + 0.35, w) for i, w in enumerate(words.split())]
    return Segment(track=track, start=ws[0].start, end=ws[-1].end, text=words, words=ws,
                   chunk_start=start, chunk_end=ws[-1].end, speech_end=ws[-1].end, id=sid)


DIAR = {
    "engine": "basic", "space": "test",
    "exclusive": [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
        {"start": 2.0, "end": 4.0, "speaker": "SPEAKER_01"},
        {"start": 10.0, "end": 12.0, "speaker": "SPEAKER_00"},
    ],
    "speakers": [{"label": "SPEAKER_00", "embedding": [1.0, 0.0]}, {"label": "SPEAKER_01", "embedding": [0.0, 1.0]}],
}


def test_speaker_change_inside_one_chunk_splits_the_line():
    s = seg("system", 0.0, "one two three four five six seven eight nine ten")  # 0.0 .. 3.95
    labels = assign_words(s.words, Turns(DIAR["exclusive"]))
    parts = runs(s.words, labels)
    assert [(lab, " ".join(w.text for w in ws)) for lab, ws in parts] == [
        ("SPEAKER_00", "one two three four five"),
        ("SPEAKER_01", "six seven eight nine ten"),
    ]


def test_single_word_wobble_is_smoothed_and_gaps_follow_neighbours():
    turns = Turns([
        {"start": 0.0, "end": 1.0, "speaker": "A"},
        {"start": 1.0, "end": 1.3, "speaker": "B"},  # one short word's worth
        {"start": 1.3, "end": 3.0, "speaker": "A"},
    ])
    words = [Word(0.2, 0.5, "a"), Word(1.05, 1.25, "b"), Word(1.5, 1.9, "c"), Word(8.0, 8.3, "far")]
    assert assign_words(words, turns) == ["A", "A", "A", "A"]


def test_lines_interleave_both_tracks_and_apply_rules():
    speakers = merge.build_speakers(DIAR)
    assert list(speakers["speakers"]) == ["S1", "S2"]
    segments = [
        seg("system", 0.0, "hello from Collect one two three four five six"),
        seg("mic", 5.0, "my reply"),
        seg("system", 10.0, "back again"),
    ]
    vocab.add_rule("Collect", "ExampleCo")
    lines = merge.build_lines(segments, DIAR, speakers)
    assert [(l["speaker"], l["text"]) for l in lines] == [
        ("S1", "hello from ExampleCo one two"),
        ("S2", "three four five six"),
        ("me", "my reply"),
        ("S1", "back again"),
    ]
    assert [l["id"] for l in lines] == [0, 1, 2, 3]


def test_same_speaker_carrying_on_becomes_one_paragraph():
    segments = [seg("mic", 0.0, "first part"), seg("mic", 1.2, "second part"), seg("mic", 20.0, "much later")]
    lines = merge.build_lines(segments, None, {"speakers": {}})
    assert [l["text"] for l in lines] == ["first part second part", "much later"]


def test_without_diarization_the_other_side_is_them():
    lines = merge.build_lines([seg("system", 0.0, "hi there")], None, {"speakers": {}})
    assert lines[0]["speaker"] == "them"
    assert merge.display_name("them", {}, "Taylor") == "Them"


def test_names_carry_over_a_rerun():
    previous = {"turns": DIAR["exclusive"], "named": {"SPEAKER_01": {"name": "Anna", "remember": True}}}
    rerun = {"engine": "basic", "space": "test", "speakers": [], "exclusive": [
        {"start": 0.0, "end": 2.0, "speaker": "X"}, {"start": 2.0, "end": 4.1, "speaker": "Y"}]}
    speakers = merge.build_speakers(rerun, previous)["speakers"]
    assert speakers["S2"]["name"] == "Anna" and speakers["S2"]["remember"] is True
    assert speakers["S1"]["name"] is None


def test_speaker_clip_is_the_longest_stretch_capped():
    info = speaker_summary([
        {"start": 0, "end": 2, "speaker": "A"}, {"start": 5, "end": 25, "speaker": "A"}, {"start": 30, "end": 31, "speaker": "B"}])
    a, b = info["A"]["clip"]
    assert 5 <= a and b <= 25.2 and 7.5 <= b - a <= 8.5
    assert info["A"]["duration"] == 22.0 and info["B"]["first"] == 30


def test_markdown_format():
    meta = {"created": "2026-11-11T16:15:00+01:00", "duration": 3725, "title": "Weekly sync", "user_name": "Taylor"}
    speakers = {"speakers": {"S1": {"label": "A", "name": "Anna"}, "S2": {"label": "B", "name": None}}}
    lines = [
        {"start": 3.2, "speaker": "me", "text": "Hi."},
        {"start": 65.0, "speaker": "S1", "text": "Hello."},
        {"start": 3700.0, "speaker": "S2", "text": "Bye."},
    ]
    md = markdown.render(meta, lines, speakers)
    assert md.startswith("# Weekly sync\n")
    assert "- **Date:** 2026-11-11 16:15" in md and "- **Duration:** 1:02:05" in md
    assert "- **Participants:** Taylor, Anna, Speaker 2" in md
    assert "**[00:03] Taylor:** Hi." in md and "**[01:05] Anna:** Hello." in md and "**[1:01:40] Speaker 2:** Bye." in md


def test_output_path_is_date_plus_title_and_never_clobbers(tmp_path):
    meta = {"created": "2026-11-11T16:15:00+01:00", "title": "Q4: plan / review?"}
    p = markdown.output_path(tmp_path, meta)
    assert p.name == "2026-11-11 1615 Q4 plan review.md"
    p.write_text("x")
    assert markdown.output_path(tmp_path, meta).name == "2026-11-11 1615 Q4 plan review (2).md"
    assert markdown.output_path(tmp_path, meta, current=p) == p  # re-saving the same session reuses its file


def test_rules_respect_word_boundaries_and_case():
    rules = vocab.compile_rules([{"wrong": "collect", "right": "ExampleCo"}, {"wrong": "collect app", "right": "ExampleCo app"}])
    assert vocab.apply_rules("The Collect app and collect, not collection.", rules) == "The ExampleCo app and ExampleCo, not collection."


def test_fix_from_transcript_saves_rule_and_term():
    vocab.add_rule("Collect", "ExampleCo", "ExampleCo work")
    assert vocab.list_rules()[0]["right"] == "ExampleCo"
    assert vocab.list_sets() == [{"name": "ExampleCo work", "terms": ["ExampleCo"]}]
    assert vocab.build_prompt(["ExampleCo work"], ["Anna"]) == "Glossary: ExampleCo, Anna."
    assert vocab.build_prompt([], []) == vocab.STYLE_PROMPT  # inactive sets are not sent


def test_prompt_stays_under_whisper_limit_newest_first():
    vocab.replace_all([{"name": "Big", "terms": [f"term{i:04d}" for i in range(400)]}], [])
    prompt = vocab.build_prompt(["Big"])
    assert len(prompt) <= vocab.PROMPT_CHARS + 12
    assert "term0399" in prompt and "term0000" not in prompt


def test_voiceprints_match_suggest_and_forget():
    voiceprints.add("Anna", [1.0, 0.0, 0.0], "space-a")
    voiceprints.add("Bo", [0.0, 1.0, 0.0], "space-a")
    assert voiceprints.match([0.9, 0.1, 0.0], "space-a")[0] == "Anna"
    assert voiceprints.match([0.9, 0.1, 0.0], "other-space") is None  # embeddings from another model never match
    assert voiceprints.match([0.0, 0.0, 1.0], "space-a") is None
    got = voiceprints.suggest(
        [{"label": "X", "embedding": [1.0, 0.05, 0.0]}, {"label": "Y", "embedding": [0.8, 0.3, 0.0]}], "space-a")
    assert got["X"][0] == "Anna" and "Y" not in got  # one saved voice names one speaker
    assert voiceprints.forget("Anna") == 1
    assert voiceprints.names() == ["Bo"]


def test_prompt_follows_the_language():
    assert vocab.build_prompt([], [], "da") == vocab.STYLE_PROMPTS["da"]
    assert vocab.build_prompt([], [], "auto") == ""  # an English sentence would pull other languages towards English
    assert vocab.build_prompt([], [], "el") == ""
    vocab.add_term("ExampleCo", "Work")
    assert vocab.build_prompt(["Work"], [], "en") == "Glossary: ExampleCo."
    assert vocab.build_prompt(["Work"], [], "da") == "ExampleCo."
