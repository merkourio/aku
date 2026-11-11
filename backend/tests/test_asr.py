import numpy as np

from aku.asr.base import Word, is_non_speech, spread_words, tokens_to_words
from aku.asr.chunker import SR, Chunk, Chunker
from aku.asr.cloud_openai import OpenAIBackend
from aku.asr.live import segment_from
from aku.asr.vad import FRAME


def run_chunker(pattern):
    """pattern: list of (seconds, speech probability). Audio samples carry the frame index."""
    probs = []
    for seconds, p in pattern:
        probs += [p] * int(seconds * SR / FRAME)
    it = iter(probs)
    chunks: list[Chunk] = []
    c = Chunker("system", lambda frame: next(it), chunks.append)
    audio = np.arange(len(probs) * FRAME, dtype=np.int64).astype(np.int16)
    for i in range(0, len(audio), 1000):  # odd block size on purpose
        c.feed(audio[i : i + 1000], i)
    c.flush()
    return chunks


def test_chunk_closes_at_a_pause():
    chunks = run_chunker([(1.0, 0.0), (3.0, 0.9), (1.0, 0.0), (2.0, 0.9), (1.0, 0.0)])
    assert len(chunks) == 2
    a, b = chunks
    assert 0.7 <= a.start_s <= 1.0 and 4.0 <= a.end_s <= 4.3  # pre-roll and post-roll around the speech
    assert a.valid_start == a.start_s and a.valid_end == a.end_s
    assert b.start_s >= a.end_s
    assert abs(a.speech_end - 4.0) < 0.05


def test_short_blip_is_dropped():
    assert run_chunker([(1.0, 0.0), (0.1, 0.9), (1.0, 0.0)]) == []


def test_long_speech_is_cut_at_the_quietest_point_with_overlap():
    # 30 s of speech with a brief dip (not a full pause) at 10 s
    chunks = run_chunker([(10.0, 0.9), (0.2, 0.1), (9.8, 0.9), (0.2, 0.1), (10.0, 0.9), (1.0, 0.0)])
    assert len(chunks) >= 3
    first, second = chunks[0], chunks[1]
    assert first.end_s - first.start_s <= 12.5
    assert 9.9 <= first.valid_end <= 10.3  # cut landed in the dip
    assert second.valid_start == first.valid_end
    assert second.start_s < first.valid_end < first.end_s  # the two overlap around the cut
    # valid ranges tile the speech without gaps or double cover
    for a, b in zip(chunks, chunks[1:]):
        assert a.valid_end == b.valid_start


def test_words_in_an_overlap_are_kept_once():
    chunks = run_chunker([(10.0, 0.9), (0.2, 0.1), (9.8, 0.9), (1.0, 0.0)])
    first, second = chunks[0], chunks[1]
    cut = first.valid_end
    # both chunks "hear" the same word straddling the cut region
    w_abs = (cut - 0.15, cut - 0.02)
    seg1 = segment_from(first, [Word(w_abs[0] - first.start_s, w_abs[1] - first.start_s, "before")], "x")
    seg2 = segment_from(second, [Word(w_abs[0] - second.start_s, w_abs[1] - second.start_s, "before")], "x")
    assert seg1 is not None and seg2 is None


def test_tokens_merge_into_words():
    words = tokens_to_words([(0.0, 0.2, " my"), (0.2, 0.4, " ante"), (0.4, 0.6, "ced"), (0.6, 0.8, "ents"), (0.8, 0.9, ","), (1.0, 1.2, " nor")])
    assert [w.text for w in words] == ["my", "antecedents,", "nor"]
    assert words[1].start == 0.2 and words[1].end == 0.9


def test_non_speech_tags():
    assert is_non_speech("[BLANK_AUDIO]") and is_non_speech(" (music) ") and is_non_speech("")
    assert not is_non_speech("Hello (again)")


def test_spread_words_covers_the_span():
    words = spread_words("one two three", 2.0, 5.0)
    assert [w.text for w in words] == ["one", "two", "three"]
    assert words[0].start == 2.0 and abs(words[-1].end - 5.0) < 1e-6


class FakeOpenAI:
    """Stands in for the OpenAI client; records what was asked."""

    def __init__(self, response):
        self.response = response
        self.calls = []
        self.audio = self
        self.transcriptions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class R:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_openai_whisper1_word_timestamps():
    client = FakeOpenAI(R(text="Hello, ExampleCo team.", words=[R(word="Hello", start=0.1, end=0.4), R(word="ExampleCo", start=0.5, end=0.9), R(word="team", start=0.9, end=1.2)]))
    b = OpenAIBackend("", "whisper-1", client=client)
    words = b.transcribe(np.zeros(SR * 2, dtype=np.int16), prompt="Glossary: ExampleCo.")
    assert [w.text for w in words] == ["Hello,", "ExampleCo", "team."]  # punctuation restored from the text
    call = client.calls[0]
    assert call["timestamp_granularities"] == ["word"] and call["response_format"] == "verbose_json"
    assert call["prompt"] == "Glossary: ExampleCo."


def test_openai_text_only_model_spreads_words():
    client = FakeOpenAI(R(text="just some text"))
    b = OpenAIBackend("", "gpt-4o-transcribe", client=client)
    assert not b.word_timestamps
    words = b.transcribe(np.zeros(SR * 3, dtype=np.int16))
    assert len(words) == 3 and abs(words[-1].end - 3.0) < 1e-6
    assert "timestamp_granularities" not in client.calls[0]


def chunk(track, speech_seconds):
    return Chunk(track=track, start=0, audio=np.zeros(SR, dtype=np.int16), valid_start=0.0, valid_end=1.0,
                 speech_end=1.0, speech_seconds=speech_seconds)


def test_fixed_language_is_always_used():
    from aku.asr.live import LanguagePicker

    picker = LanguagePicker("da")
    assert picker.choose(chunk("mic", 0.5)) == "da" and picker.choose(chunk("system", 9.0)) == "da"


def test_automatic_trusts_long_chunks_and_reuses_the_track_language_for_short_ones():
    from aku.asr.live import LanguagePicker

    picker = LanguagePicker("auto")
    assert picker.choose(chunk("system", 1.0)) == "auto"  # nothing known yet
    picker.learn(chunk("system", 6.0), "da")
    picker.learn(chunk("mic", 6.0), "en")
    picker.learn(chunk("mic", 0.8), "sv")  # a short chunk's detection is not trusted
    assert picker.choose(chunk("system", 1.0)) == "da"  # "ja", "okay": use the track's language
    assert picker.choose(chunk("mic", 1.0)) == "en"
    assert picker.choose(chunk("system", 6.0)) == "auto"  # long enough to detect again


def test_run_chunk_records_the_spoken_language():
    from aku.asr.live import LanguagePicker, run_chunk

    class Fake:
        name, last_language = "fake", None
        def transcribe(self, audio, prompt="", language="en"):
            self.asked = (language, prompt)
            self.last_language = "da" if language == "auto" else None
            return [Word(0.1, 0.4, "hej")]

    b = Fake()
    seg = run_chunk(b, chunk("system", 5.0), LanguagePicker("auto"), lambda lang: f"prompt-{lang}")
    assert b.asked == ("auto", "prompt-auto") and seg.language == "da"
    seg = run_chunk(b, chunk("system", 5.0), LanguagePicker("el"), lambda lang: f"prompt-{lang}")
    assert b.asked == ("el", "prompt-el") and seg.language == "el"


def test_openai_automatic_leaves_the_language_out():
    client = FakeOpenAI(R(text="Hej med dig", language="danish", words=[]))
    b = OpenAIBackend("", "whisper-1", client=client)
    b.transcribe(np.zeros(SR * 2, dtype=np.int16), language="auto")
    assert "language" not in client.calls[0] and b.last_language == "da"
    b.transcribe(np.zeros(SR * 2, dtype=np.int16), language="da")
    assert client.calls[1]["language"] == "da"
