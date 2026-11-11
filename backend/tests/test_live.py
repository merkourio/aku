import numpy as np

from aku.asr.base import Word
from aku.asr.live import Segment
from aku.diarize import cluster
from aku.diarize.live import LiveDiarizer, SpeakerTracker
from aku.sessions.liveview import LiveView

SR = 16000
rng = np.random.default_rng(3)
VOICES = {name: rng.standard_normal(32) for name in "abc"}


def voice(name: str) -> np.ndarray:
    """An embedding for one window of this voice: the voice plus a little noise."""
    return (VOICES[name] + 0.15 * rng.standard_normal(32)).astype(np.float32)


def phrase(start_s: float, seconds: float, first_phrase: int):
    span = (int(start_s * SR), int((start_s + seconds) * SR))
    return [span], cluster.make_windows([span], first_phrase)


def feed(tracker: SpeakerTracker, who: str, start_s: float, seconds: float = 4.0) -> None:
    spans, windows = phrase(start_s, seconds, len(tracker.spans))
    tracker.add(spans, windows, np.stack([voice(who) for _ in windows]))


def speakers_at(turns: list[dict], *times: float) -> list[str]:
    out = []
    for t in times:
        out.append(next(turn["speaker"] for turn in turns if turn["start"] <= t < turn["end"]))
    return out


def test_windows_cover_long_phrases_and_keep_short_ones_whole():
    w = cluster.make_windows([(0, 2 * SR), (10 * SR, 14 * SR)])
    assert w[0] == (0, 0, 2 * SR)
    assert [x[0] for x in w[1:]] == [1] * (len(w) - 1) and w[1][1] == 10 * SR and w[-1][2] == 14 * SR
    assert all(b - a == cluster.WIN for _, a, b in w[1:])


def test_speaker_numbers_stay_stable_as_new_voices_appear():
    t = SpeakerTracker()
    feed(t, "a", 0)
    assert speakers_at(t.update(None), 1.0) == ["S1"]
    feed(t, "b", 5)
    assert speakers_at(t.update(None), 1.0, 6.0) == ["S1", "S2"]
    feed(t, "a", 10)
    feed(t, "c", 15)
    turns = t.update(None)
    assert speakers_at(turns, 1.0, 6.0, 11.0, 16.0) == ["S1", "S2", "S1", "S3"]
    assert t.found == 3


def test_speaker_count_can_be_lowered_and_raised_during_the_call():
    t = SpeakerTracker()
    for i, who in enumerate("abcabc"):
        feed(t, who, i * 5)
    assert t.found == 0 and len({s for s in speakers_at(t.update(None), 1, 6, 11)}) == 3
    two = speakers_at(t.update(2), 1, 6, 11, 16, 21, 26)
    assert len(set(two)) == 2 and t.found == 2
    three = speakers_at(t.update(3), 1, 6, 11, 16, 21, 26)
    assert len(set(three)) == 3 and three[:3] == three[3:]  # same voice, same number, both times it spoke
    # whoever kept their number through the merge still has it
    assert three[0] == two[0]


def test_short_phrase_follows_its_neighbour():
    spans = [(0, 4 * SR), (int(4.2 * SR), int(4.8 * SR)), (10 * SR, 14 * SR)]
    windows = cluster.make_windows(spans)
    vectors = np.stack([voice("b") if pi == 1 else voice("a" if pi == 0 else "c") for pi, _, _ in windows])
    labels, _ = cluster.label_windows(vectors, windows, None)
    short = next(i for i, w in enumerate(windows) if w[0] == 1)
    assert labels[short] == labels[0]  # "he asked." goes with the sentence before it, whatever its noisy embedding says


class FakeWorker:
    """Stands in for the voice-model process: one voice per second of audio, by position."""

    def __init__(self, plan):
        self.plan = plan  # list of (until_second, voice)
        self.calls = 0

    def start(self):
        pass

    def close(self):
        pass

    def embed(self, wav_path, windows):
        self.calls += 1
        out = []
        for a, b in windows:
            mid = (a + b) / 2 / SR
            out.append(voice(next(v for until, v in self.plan if mid < until)))
        return np.stack(out)


def test_live_diarizer_end_to_end_with_a_fake_voice_model(tmp_path, monkeypatch):
    from aku.audio import wav

    path = tmp_path / "system.wav"
    w = wav.WavWriter(path)
    w.write(np.zeros(SR * 20, dtype=np.int16))
    w.flush()
    # every analysed stretch is "speech" from its start to its end
    monkeypatch.setattr(cluster, "find_phrases", lambda pcm, vad, offset=0: [(offset, offset + len(pcm))])
    monkeypatch.setattr("aku.diarize.live.SileroVAD", lambda path: None)
    monkeypatch.setattr("aku.config.ensure_vad_model", lambda: None)
    got = []
    worker = FakeWorker([(8, "a"), (99, "b")])
    d = LiveDiarizer(path, w.flush, lambda turns, found: got.append((turns, found)), got.append, worker=worker)
    d.add(0.0, 8.0)
    d.add(7.8, 16.0)  # overlaps the first chunk a little: that audio must not be analysed twice
    d.set_speakers(2)
    import time

    for _ in range(200):  # the diarizer works on its own thread
        if got and d._queue.empty() and got[-1][1] == 2:
            break
        time.sleep(0.02)
    d.close()
    turns, found = got[-1]
    assert found == 2
    assert speakers_at(turns, 2.0, 12.0) == ["S1", "S2"]
    assert max(t["end"] for t in turns) <= 16.0 and min(t["start"] for t in turns) == 0.0
    covered = sum(t["end"] - t["start"] for t in turns)
    assert abs(covered - 16.0) < 0.01  # no stretch counted twice


def seg(track, sid, start, text):
    words = [Word(start + i, start + i + 0.9, w) for i, w in enumerate(text.split())]
    return Segment(track=track, start=words[0].start, end=words[-1].end, text=text, words=words,
                   chunk_start=start, chunk_end=words[-1].end, speech_end=words[-1].end, id=sid)


def test_live_view_splits_and_relabels_lines():
    view = LiveView("Taylor", [])
    first = view.add(seg("system", 1, 0.0, "hello there yes indeed"))
    mine = view.add(seg("mic", 2, 5.0, "my reply"))
    assert [(l["name"], l["text"]) for l in first] == [("Them", "hello there yes indeed")]
    assert mine[0]["name"] == "Taylor"

    changed = view.relabel([{"start": 0.0, "end": 2.0, "speaker": "S1"}, {"start": 2.0, "end": 4.0, "speaker": "S2"}])
    assert list(changed) == [1]  # only the segment whose labels changed is sent again
    assert [(l["name"], l["text"], l["segment"]) for l in changed[1]] == [
        ("Speaker 1", "hello there", 1), ("Speaker 2", "yes indeed", 1)]
    assert view.relabel([{"start": 0.0, "end": 2.0, "speaker": "S1"}, {"start": 2.0, "end": 4.0, "speaker": "S2"}]) == {}

    # a later segment is labelled straight away with what is known
    later = view.add(seg("system", 3, 2.5, "more words"))
    assert later[0]["speaker"] == "S2"
    assert [l["name"] for l in view.all()] == ["Speaker 1", "Speaker 2", "Speaker 2", "Taylor"]
    ids = [l["id"] for l in view.all()]
    assert len(set(ids)) == len(ids)
