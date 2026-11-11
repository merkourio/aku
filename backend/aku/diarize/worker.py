"""Diarization worker. Runs as its own process so torch's memory is freed afterwards.

After a call:
    python -m aku.diarize.worker --wav system.wav --out diarization.json --engine pyannote --speakers auto
  Prints JSON lines on stdout: {"progress": 0..1, "step": "..."} and finally {"done": true}.

During a call (live speaker separation) and for voiceprints of cloud-labelled speakers:
    python -m aku.diarize.worker --serve
  Prints {"ready": true}, then answers one JSON line per request on stdin:
    {"wav": "path", "windows": [[start, end], ...]}  ->  {"vectors": "<base64 float32>", "dim": 256}

The Hugging Face token comes in via the AKU_HF_TOKEN environment variable.

Engines:
  pyannote  pyannote/speaker-diarization-community-1 (needs a token and accepted model conditions)
  basic     no token needed: VAD phrases + WeSpeaker embeddings + clustering (see cluster.py)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import warnings
from pathlib import Path

import numpy as np

SR = 16000
PYANNOTE_PIPELINE = "pyannote/speaker-diarization-community-1"
BASIC_EMBEDDING = "pyannote/wespeaker-voxceleb-resnet34-LM"
BASIC_SPACE = "wespeaker-resnet34-LM"


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


class Embedder:
    """WeSpeaker embeddings for windows of audio."""

    def __init__(self, token: str | None):
        import torch
        from pyannote.audio.pipelines.speaker_verification import PretrainedSpeakerEmbedding

        self.torch = torch
        self.model = PretrainedSpeakerEmbedding(BASIC_EMBEDDING, device=torch.device("cpu"), token=token or None)
        self.dimension = self.model.dimension

    def embed(self, clips: list[np.ndarray], progress=None) -> np.ndarray:
        """clips: float32 audio per window. Returns (len(clips), dimension)."""
        torch = self.torch
        vectors = np.zeros((len(clips), self.dimension), dtype=np.float32)
        order = sorted(range(len(clips)), key=lambda i: len(clips[i]))
        batch = 16
        with torch.inference_mode():
            for n in range(0, len(order), batch):
                idx = order[n : n + batch]
                longest = max(max(len(clips[i]) for i in idx), 400)
                wave = torch.zeros((len(idx), 1, longest))
                mask = torch.zeros((len(idx), longest))
                for row, i in enumerate(idx):
                    k = len(clips[i])
                    wave[row, 0, :k] = torch.from_numpy(clips[i])
                    mask[row, :k] = 1
                vectors[idx] = self.model(wave, masks=mask)
                if progress:
                    progress(min(1.0, (n + batch) / len(order)))
        return vectors


def run_basic(audio: np.ndarray, speakers: int | None, token: str | None) -> dict:
    from aku import config
    from aku.asr.vad import SileroVAD
    from aku.diarize import cluster

    say(progress=0.02, step="Finding speech")
    spans = cluster.find_phrases((audio * 32767).astype(np.int16), SileroVAD(config.ensure_vad_model()))
    if not spans:
        return {"engine": "basic", "space": BASIC_SPACE, "turns": [], "exclusive": [], "speakers": []}
    windows = cluster.make_windows(spans)

    say(progress=0.05, step="Loading voice model")
    embedder = Embedder(token)
    vectors = embedder.embed(
        [audio[a:b] for _, a, b in windows],
        progress=lambda p: say(progress=0.1 + 0.8 * p, step="Comparing voices"),
    )

    say(progress=0.92, step="Grouping voices")
    labels, centroids = cluster.label_windows(vectors, windows, speakers)
    turns = cluster.turns_from(spans, windows, labels)

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
    return {"engine": "basic", "space": BASIC_SPACE, "turns": turns, "exclusive": turns, "speakers": speakers_out}


# --- embedding server -------------------------------------------------------------


def serve(token: str | None) -> int:
    from aku.audio import wav

    embedder = Embedder(token)
    say(ready=True, dim=embedder.dimension)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            path = Path(req["wav"])
            clips = [wav.read(path, a, b - a).astype(np.float32) / 32768.0 for a, b in req["windows"]]
            vectors = embedder.embed(clips) if clips else np.zeros((0, embedder.dimension), dtype=np.float32)
            say(vectors=base64.b64encode(vectors.astype("<f4").tobytes()).decode(), dim=embedder.dimension)
        except Exception as e:  # noqa: BLE001 - the parent decides what to do
            say(error=f"{type(e).__name__}: {e}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--wav")
    ap.add_argument("--out")
    ap.add_argument("--engine", choices=["pyannote", "basic"], default="basic")
    ap.add_argument("--speakers", default="auto")
    args = ap.parse_args()
    warnings.filterwarnings("ignore")
    token = os.environ.get("AKU_HF_TOKEN") or None
    if args.serve:
        try:
            return serve(token)
        except Exception as e:  # noqa: BLE001
            say(error=f"{type(e).__name__}: {e}")
            return 1
    speakers = None if args.speakers == "auto" else int(args.speakers)
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
