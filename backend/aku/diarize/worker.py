"""Diarization worker. Runs as its own process so torch's memory is freed afterwards.

    python -m aku.diarize.worker --wav system.wav --out diarization.json --engine pyannote --speakers auto

Prints JSON lines on stdout: {"progress": 0..1, "step": "..."} and finally {"done": true}.
The Hugging Face token comes in via the AKU_HF_TOKEN environment variable.

Engines:
  pyannote  pyannote/speaker-diarization-community-1 (needs a token and accepted model conditions)
  basic     no token needed: VAD phrases + WeSpeaker embeddings + clustering
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

import numpy as np

SR = 16000
PYANNOTE_PIPELINE = "pyannote/speaker-diarization-community-1"
BASIC_EMBEDDING = "pyannote/wespeaker-voxceleb-resnet34-LM"


def say(**msg) -> None:
    print(json.dumps(msg), flush=True)


def load_audio(path: Path) -> np.ndarray:
    from aku.audio import wav

    return wav.read(path).astype(np.float32) / 32768.0


# --- pyannote -----------------------------------------------------------------


def run_pyannote(audio: np.ndarray, speakers: int | None, token: str | None) -> dict:
    import torch
    from pyannote.audio import Pipeline

    say(progress=0.02, step="Loading pyannote")
    pipeline = Pipeline.from_pretrained(PYANNOTE_PIPELINE, token=token)
    if pipeline is None:
        raise RuntimeError("Could not load the pyannote pipeline. Check the Hugging Face token and model conditions.")

    steps = {"segmentation": (0.05, 0.45), "embeddings": (0.45, 0.95)}

    def hook(step_name, step_artifact, file=None, total=None, completed=None):
        if step_name in steps and total:
            lo, hi = steps[step_name]
            say(progress=lo + (hi - lo) * completed / total, step=f"Separating speakers ({step_name})")

    out = pipeline(
        {"waveform": torch.from_numpy(audio)[None, :], "sample_rate": SR},
        num_speakers=speakers,
        hook=hook,
    )

    def turns(annotation) -> list[dict]:
        return [
            {"start": round(seg.start, 3), "end": round(seg.end, 3), "speaker": label}
            for seg, _, label in annotation.itertracks(yield_label=True)
        ]

    labels = out.speaker_diarization.labels()
    embeddings = out.speaker_embeddings
    speakers_out = []
    for i, label in enumerate(labels):
        emb = None
        if embeddings is not None and i < len(embeddings) and np.all(np.isfinite(embeddings[i])):
            emb = [float(x) for x in embeddings[i]]
        speakers_out.append({"label": label, "embedding": emb})
    return {
        "engine": "pyannote",
        "space": "community-1",
        "turns": turns(out.speaker_diarization),
        "exclusive": turns(out.exclusive_speaker_diarization),
        "speakers": speakers_out,
    }


# --- basic ----------------------------------------------------------------------


def phrases(audio: np.ndarray) -> list[tuple[int, int]]:
    """Speech stretches separated by short pauses (speaker changes usually sit on one)."""
    from aku import config
    from aku.asr.vad import FRAME, SileroVAD

    vad = SileroVAD(config.ensure_vad_model())
    pcm = (audio * 32767).astype(np.int16)
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
    return [(a, b) for a, b in out if b - a >= int(0.2 * SR)]


def run_basic(audio: np.ndarray, speakers: int | None, token: str | None) -> dict:
    import torch
    from pyannote.audio.pipelines.speaker_verification import PretrainedSpeakerEmbedding
    from sklearn.cluster import AgglomerativeClustering

    say(progress=0.02, step="Finding speech")
    spans = phrases(audio)
    if not spans:
        return {"engine": "basic", "space": "wespeaker-resnet34-LM", "turns": [], "exclusive": [], "speakers": []}

    # windows: short phrases whole, long phrases in overlapping 1.5 s windows
    win, hop = int(1.5 * SR), int(0.5 * SR)
    windows: list[tuple[int, int, int]] = []  # (phrase index, start, end)
    for pi, (a, b) in enumerate(spans):
        if b - a <= int(2.5 * SR):
            windows.append((pi, a, b))
        else:
            for s in range(a, b - win + 1, hop):
                windows.append((pi, s, s + win))
            if (b - a - win) % hop:
                windows.append((pi, b - win, b))

    say(progress=0.05, step="Loading voice model")
    embed = PretrainedSpeakerEmbedding(BASIC_EMBEDDING, device=torch.device("cpu"), token=token or None)
    vectors = np.zeros((len(windows), embed.dimension), dtype=np.float32)
    order = sorted(range(len(windows)), key=lambda i: windows[i][2] - windows[i][1])
    batch = 16
    with torch.inference_mode():
        for n in range(0, len(order), batch):
            idx = order[n : n + batch]
            longest = max(windows[i][2] - windows[i][1] for i in idx)
            wave = torch.zeros((len(idx), 1, longest))
            mask = torch.zeros((len(idx), longest))
            for row, i in enumerate(idx):
                _, a, b = windows[i]
                wave[row, 0, : b - a] = torch.from_numpy(audio[a:b])
                mask[row, : b - a] = 1
            vectors[idx] = embed(wave, masks=mask)
            say(progress=0.1 + 0.8 * min(1.0, (n + batch) / len(order)), step="Comparing voices")

    good = np.all(np.isfinite(vectors), axis=1)
    vectors[~good] = 0
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    unit = vectors / np.maximum(norms, 1e-9)
    length = np.array([b - a for _, a, b in windows])
    reliable = good & (length >= int(1.0 * SR))  # short windows give noisy embeddings
    if reliable.sum() < 2:
        reliable = good.copy()

    say(progress=0.92, step="Grouping voices")
    labels = np.zeros(len(windows), dtype=int)
    ridx = np.flatnonzero(reliable)
    if len(ridx) >= 2:
        if speakers:
            model = AgglomerativeClustering(n_clusters=min(speakers, len(ridx)), metric="cosine", linkage="average")
        else:
            model = AgglomerativeClustering(n_clusters=None, distance_threshold=0.75, metric="cosine", linkage="average")
        rl = model.fit_predict(unit[ridx])
        if not speakers:
            # fold tiny clusters (stray noises, laughter) into the nearest real one
            dur = {c: length[ridx][rl == c].sum() for c in set(rl)}
            big = [c for c in dur if dur[c] >= max(3.0 * SR, 0.03 * length[ridx].sum())] or [max(dur, key=dur.get)]
            cents = {c: unit[ridx][rl == c].mean(axis=0) for c in big}
            for k, c in enumerate(rl):
                if c not in cents:
                    rl[k] = max(cents, key=lambda b: float(unit[ridx][k] @ cents[b]))
        labels[ridx] = rl
    centroids = {}
    for c in sorted(set(labels[ridx])) if len(ridx) else [0]:
        v = unit[ridx][labels[ridx] == c].mean(axis=0) if len(ridx) else unit.mean(axis=0)
        centroids[c] = v / max(np.linalg.norm(v), 1e-9)
    for i in np.flatnonzero(~reliable):
        if good[i]:
            labels[i] = max(centroids, key=lambda c: float(unit[i] @ centroids[c]))
        else:
            labels[i] = -1

    # turns: every 0.1 s of a phrase takes the label of the window centred closest to it
    step = int(0.1 * SR)
    turns: list[dict] = []
    by_phrase: dict[int, list[int]] = {}
    for i, (pi, _, _) in enumerate(windows):
        by_phrase.setdefault(pi, []).append(i)
    previous = None
    for pi, (a, b) in enumerate(spans):
        wins = [i for i in by_phrase[pi] if labels[i] >= 0]
        if not wins:
            lab_at = lambda t: previous if previous is not None else 0
        else:
            centres = np.array([(windows[i][1] + windows[i][2]) / 2 for i in wins])
            lab_at = lambda t, centres=centres, wins=wins: int(labels[wins[int(np.argmin(np.abs(centres - t)))]])
        t = a
        while t < b:
            e = min(t + step, b)
            lab = lab_at((t + e) / 2)
            if turns and turns[-1]["speaker"] == lab and abs(turns[-1]["end"] - t / SR) < 1e-6:
                turns[-1]["end"] = e / SR
            else:
                turns.append({"start": t / SR, "end": e / SR, "speaker": lab})
            previous = lab
            t = e

    # name speakers in order of appearance
    names: dict[int, str] = {}
    for t in turns:
        names.setdefault(t["speaker"], f"SPEAKER_{len(names):02d}")
    for t in turns:
        t["speaker"] = names[t["speaker"]]
        t["start"], t["end"] = round(t["start"], 3), round(t["end"], 3)
    speakers_out = [
        {"label": names[c], "embedding": [float(x) for x in centroids[c]] if c in centroids else None}
        for c in names
    ]
    return {"engine": "basic", "space": "wespeaker-resnet34-LM", "turns": turns, "exclusive": turns, "speakers": speakers_out}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--engine", choices=["pyannote", "basic"], default="basic")
    ap.add_argument("--speakers", default="auto")
    args = ap.parse_args()
    warnings.filterwarnings("ignore")
    speakers = None if args.speakers == "auto" else int(args.speakers)
    token = os.environ.get("AKU_HF_TOKEN") or None
    try:
        audio = load_audio(Path(args.wav))
        result = (run_pyannote if args.engine == "pyannote" else run_basic)(audio, speakers, token)
        result["requested_speakers"] = speakers
        tmp = Path(args.out).with_suffix(".tmp")
        tmp.write_text(json.dumps(result))
        tmp.replace(args.out)
        say(progress=1.0, step="Done", done=True)
        return 0
    except Exception as e:  # noqa: BLE001 - reported to the parent process
        say(error=f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
