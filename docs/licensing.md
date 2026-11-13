# Licensing and attribution

Aku is AGPL-3.0-or-later. The complete license is in LICENSE and packaged with
the wheel/source distribution. Its source is https://github.com/merkourio/aku;
release source archives match the release tag. Dependencies are separately
licensed; Aku's license does not relicense them.

- whisper.cpp / ggml: MIT, https://github.com/ggml-org/whisper.cpp.
  The exact source commit is pinned in scripts/build-whisper.sh. Binary archives
  include the MIT notice plus notices for cpp-httplib, nlohmann/json,
  miniaudio/dr_libs and stb_vorbis. BUILD.txt records build details.
- Vue, Pinia, Vue Router and related frontend dependencies: notices generated
  from the lockfile and installed package license files in backend/licenses/frontend.txt.
  Regenerate with python3 scripts/frontend-notices.py after npm ci/build.
- pyannote.audio: MIT, https://github.com/pyannote/pyannote-audio.
- Silero VAD: MIT, https://github.com/snakers4/silero-vad.
- WeSpeaker embedding model: CC BY 4.0,
  https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM.
- Optional diarization pipeline: its model card/conditions apply separately,
  https://huggingface.co/pyannote/speaker-diarization-community-1.
- Test corpora, downloaded separately and not shipped in Aku releases:
  Mini LibriSpeech (CC BY 4.0), https://www.openslr.org/31/;
  FLEURS (CC BY 4.0), https://huggingface.co/datasets/google/fleurs.

Python dependencies installed by the package manager retain their own notices
in their distributions. Models are downloaded separately, not relicensed or
bundled into Aku's wheel. Consult the publishers' model cards and conditions.
