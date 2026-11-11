import numpy as np

from aku.audio import pipewire, wav
from aku.audio.capture import SR, ClockedTrack


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def block(n, value=1000):
    return np.full(n, value, dtype=np.int16)


def test_wav_roundtrip_and_repair(tmp_path):
    p = tmp_path / "a.wav"
    w = wav.WavWriter(p)
    w.write(block(1600))
    w.flush()
    # a crash: header never fixed
    assert wav.frames(p) == 1600
    assert wav.repair(p) == 1600
    import soundfile as sf

    data, sr = sf.read(p, dtype="int16")
    assert sr == SR and len(data) == 1600 and data[0] == 1000
    assert len(wav.read(p, 100, 50)) == 50


def test_track_pads_late_start_and_matches_clock(tmp_path):
    clock = FakeClock()
    t = ClockedTrack(tmp_path / "t.wav", clock.t, clock)
    clock.t += 0.5  # first block arrives half a second in
    t.feed(block(1024), clock.t)
    assert t.written == int(0.5 * SR)
    for _ in range(50):
        clock.t += 1024 / SR
        t.feed(block(1024), clock.t)
    clock.t += 0.01
    t.finish(clock.t)
    assert abs(t.written - (clock.t - 100.0) * SR) <= 1
    assert wav.frames(tmp_path / "t.wav") == t.written


def test_track_pads_a_dropout_with_silence(tmp_path):
    clock = FakeClock()
    t = ClockedTrack(tmp_path / "t.wav", clock.t, clock)
    got = []
    t.listeners.append(lambda pcm, start: got.append((start, pcm.copy())))
    for _ in range(20):
        clock.t += 1024 / SR
        t.feed(block(1024), clock.t)
    before = t.written
    for _ in range(12):  # stream stalls for 3 s; the reader thread ticks meanwhile
        clock.t += 0.25
        t.tick(clock.t)
    assert t.written > before + 2 * SR  # timeline kept moving
    clock.t += 1024 / SR
    t.feed(block(1024, 2000), clock.t)
    # the block after the gap sits where the clock says it should
    start, pcm = got[-1]
    assert pcm[0] == 2000
    assert abs(start + 1024 - (clock.t - 100.0) * SR) <= 1
    # listeners saw one unbroken timeline
    pos = 0
    for start, pcm in got:
        assert start == pos
        pos += len(pcm)


def test_track_corrects_slow_drift(tmp_path):
    clock = FakeClock()
    t = ClockedTrack(tmp_path / "t.wav", clock.t, clock)
    # audio clock runs 1% slow: 1024 samples take 1.01x as long
    for _ in range(int(40 * SR / 1024)):
        clock.t += 1024 / SR * 1.01
        t.feed(block(1024), clock.t)
    assert abs(t.written - (clock.t - 100.0) * SR) < 0.1 * SR
    assert t.padded > 0


def test_track_ignores_blocks_after_stop(tmp_path):
    clock = FakeClock()
    t = ClockedTrack(tmp_path / "t.wav", clock.t, clock)
    clock.t += 1.0
    t.feed(block(SR), clock.t)
    t.finish(clock.t)
    t.feed(block(1024), clock.t + 0.1)
    assert t.written == SR


DUMP = [
    {"id": 1, "type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {
        "node.name": "alsa_output.x", "node.description": "Headphones", "media.class": "Audio/Sink"}}},
    {"id": 2, "type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {
        "node.name": "alsa_input.y", "node.description": "Yeti", "media.class": "Audio/Source"}}},
    {"id": 3, "type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {
        "node.name": "teams", "media.class": "Stream/Output/Audio", "application.name": "Chromium",
        "application.process.binary": "teams-for-linux", "media.name": "Playback"}}},
    {"id": 4, "type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {
        "node.name": "spotify", "media.class": "Stream/Output/Audio", "application.name": "Spotify"}}},
    {"id": 5, "type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {
        "node.name": "aku-system-1", "media.class": "Stream/Input/Audio", "application.name": "Aku"}}},
    {"id": 31, "type": "PipeWire:Interface:Port", "info": {"direction": "output", "props": {"node.id": 3, "audio.channel": "FL"}}},
    {"id": 32, "type": "PipeWire:Interface:Port", "info": {"direction": "output", "props": {"node.id": 3, "audio.channel": "FR"}}},
    {"id": 41, "type": "PipeWire:Interface:Port", "info": {"direction": "output", "props": {"node.id": 4, "audio.channel": "MONO"}}},
    {"id": 51, "type": "PipeWire:Interface:Port", "info": {"direction": "input", "props": {"node.id": 5, "audio.channel": "FL"}}},
    {"id": 52, "type": "PipeWire:Interface:Port", "info": {"direction": "input", "props": {"node.id": 5, "audio.channel": "FR"}}},
    {"id": 60, "type": "PipeWire:Interface:Link", "info": {"output-port-id": 31, "input-port-id": 51}},
    {"id": 70, "type": "PipeWire:Interface:Metadata", "metadata": [
        {"key": "default.audio.sink", "value": {"name": "alsa_output.x"}},
        {"key": "default.audio.source", "value": {"name": "alsa_input.y"}}]},
]


def test_devices_and_apps():
    g = pipewire.parse(DUMP)
    d = pipewire.list_devices(g)
    assert d["inputs"] == [{"name": "alsa_input.y", "description": "Yeti", "default": True}]
    assert d["outputs"][0]["default"] is True
    keys = {a["key"]: a["label"] for a in d["apps"]}
    assert keys == {"teams-for-linux": "Chromium (teams-for-linux)", "Spotify": "Spotify"}  # our own node is hidden


def test_wanted_links_only_for_chosen_apps_and_only_missing():
    g = pipewire.parse(DUMP)
    assert pipewire.wanted_links(g, "aku-system-1", ["teams-for-linux"]) == [(32, 52)]  # FL already linked
    assert sorted(pipewire.wanted_links(g, "aku-system-1", ["Spotify"])) == [(41, 51), (41, 52)]  # mono to both
    assert pipewire.wanted_links(g, "aku-system-1", []) == []
