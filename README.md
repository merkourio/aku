# Aku

Aku (from Greek "άκου", listen) records a call on Linux as two tracks, your microphone and the computer's audio, and transcribes both while you talk. After the call it tells the other speakers apart, lets you name them from a short clip, and saves a Markdown transcript.

Transcription runs on this computer by default (whisper.cpp on the GPU). OpenAI is an optional cloud engine.

> **Hobby project, built with AI assistance.** Useful for my own workflow, but
> not independently security-audited or guaranteed accurate. Issues and pull
> requests are welcome; there is no promised response time or ongoing support.

**Privacy:** obtain participants' permission before recording. Local processing
is the default; optional cloud engines upload audio and may incur API charges.
Voiceprints, recordings and exports can contain sensitive information. Read the
[privacy notes](docs/privacy.md) and [security boundary](SECURITY.md).

## Install

Aku targets **Linux x86-64 with PipeWire** (developed on Fedora). The bundled
Vulkan engine needs an AVX2-capable CPU, a Vulkan loader and working GPU drivers;
other distributions/hardware are not yet a compatibility guarantee.

Install PipeWire tools, `curl`, Python 3, and [uv](https://docs.astral.sh/uv/getting-started/installation/).
No GitHub login is needed. Download and inspect the installer, then run it:

```bash
curl -fL https://github.com/merkourio/aku/releases/latest/download/get-aku.sh -o get-aku.sh
less get-aku.sh
bash get-aku.sh
```

The installer verifies release SHA-256 checksums before installing the package
and engine. It installs a Python 3.12 environment (about 1 GB on first install),
the `aku` command, a launcher entry, and **enables a background service at login**.
It does not require sudo. System prerequisites may need your distribution's
package manager; Fedora: `sudo dnf install pipewire-utils curl python3`.

Run it again to update. Use `--version v0.2.0` to select a specific release,
`--remove` to uninstall while retaining recordings/settings, or `--force` to
update despite an active recording/processing job (normally refused).

If the binary cannot run, the installer attempts a source build of the pinned
whisper.cpp revision. That needs git, CMake, C++ and Vulkan development tools.
On Fedora: `sudo dnf install git cmake gcc-c++ vulkan-headers vulkan-loader-devel glslc`.

Check everything is in place:

```bash
aku doctor
```

### From the source code

`bash scripts/install.sh` in a clone installs what is in that folder instead of a release: it builds the UI (needs `npm`) and whisper.cpp itself. Use it while developing. `bash scripts/install.sh --remove` undoes it.

### Publishing a release

See [docs/releasing.md](docs/releasing.md). `bash scripts/release.sh X.Y.Z`
prepares local artifacts only; publishing requires a separate explicit step.

## Use

1. Open **Aku** from the app launcher (or run `aku open`). It opens in your browser at `http://127.0.0.1:4587/`.
2. On **Record**, pick your microphone and what to capture from the computer: everything on an output device, or only specific apps (for example Teams). Pick the language spoken, or **Automatic**. Aku remembers these choices.
3. Press **Start recording**. Lines appear a few seconds after someone speaks.
4. While it records, the other side's lines are labelled Speaker 1, Speaker 2, … as Aku tells the voices apart. If it gets the number wrong, set **Other speakers so far**; change it again when someone joins. Leave it on **Detect** otherwise.
5. Aku separates the speakers. Play each clip, type a name, and tick **Remember this voice** if you want the name suggested next time.
6. Give the transcript a title, check the folder, and press **Finalise and save Markdown**.

Always wear headphones: on speakers, your microphone also hears the other side and their words show up twice.

### Interview mode

For an interview in the same room, where everyone talks into one microphone.

1. Once: in **Settings > Your voice**, press **Record my voice** and read aloud for 30 seconds. Aku then knows which voice is yours. (In a terminal: `aku enrol`.)
2. On **Record**, tick **Interview mode**. Aku records the microphone only. If someone also joins by call, tick **Also record the computer's audio**, and make sure everyone in the room hears the call through headphones.
3. While it records, your lines carry your name and the others show as Guest 1, Guest 2, … Set **People at the microphone** (you included) if Aku gets the number wrong.
4. After Stop, name the guests from their clips. If Aku picked the wrong voice as yours, or you never recorded your voice, choose **This is me** on the right speaker; Aku also learns your voice from that recording when you save.

Interview mode is chosen per recording and is never remembered: the hotkey and the next recording are a normal call again.

Expect more mistakes than on a call. A laptop microphone hears the room, and when two people talk at once only one of them gets the words. An external microphone placed between the speakers helps a lot.

### Language

Choose the language on the Record screen, or **Automatic** to let Aku detect it as people speak. Automatic also handles a call that mixes languages, for example Danish from the others and English from you. When you know the language, choosing it is a little more reliable, mostly for short replies like "ja" or "okay".

With a language chosen, Aku writes everything as that language: with English chosen, something said in Danish often comes out translated into English. This is a side effect of the model and not reliable; some passages stay Danish. Tick **Keep other languages as they are spoken** on the Record screen to switch it off: speech in another language is then written in that language.

The hotkey and `aku toggle` use the language and this choice from your last recording. The defaults are in Settings.

### Hotkey

The key starts a recording with your last settings, or stops the running one. Two ways to get one:

- **In Aku:** Settings > Hotkey > tick "Start and stop recording with a global shortcut". Your desktop shows a dialog to confirm the key (GNOME 48 or newer, KDE Plasma 6). **Change…** reopens that dialog.
- **In the desktop:** bind a key to the command `aku toggle`. GNOME: `bash scripts/set-hotkey-gnome.sh '<Super>F9'`, or Settings > Keyboard > View and Customise Shortcuts > Custom Shortcuts. KDE: System Settings > Shortcuts > Add New > Command.

Use one or the other, not both on the same key.

### Tray icon

The tray icon shows whether Aku is recording and has Start/Stop and Open. KDE shows it out of the box. GNOME needs the "AppIndicator and KStatusNotifierItem Support" extension.

### Fixing a misheard word

In a finished transcript, select the word, type the right spelling and press **Fix here and in future**. Aku corrects the transcript, remembers the correction for later ones and adds the right spelling to your vocabulary.

### Improve, delete audio

On a transcript page:

- **Improve transcript** re-transcribes the whole recording with a larger local model or the cloud engine. Speaker names are kept and the previous version stays as a backup in the session folder.
- **OpenAI cloud with its own speaker labels** goes further: it uploads the whole recording of the other side and lets OpenAI decide who said what. Names you already gave are carried over. OpenAI retires this model on 26 Feb 2027.
- **Delete audio, keep transcript** frees disk space (about 115 MB per hour per track). After that, Improve, re-running speaker separation and clip playback no longer work for that session.

### If Aku was closed mid-recording

The audio is written to disk continuously. On the next start the session shows as **Interrupted**; press **Recover** to finish the transcript and separate the speakers.

### If something goes wrong

- Aku keeps a log of what it does and of every error it shows you. See the latest entries with `journalctl --user -u aku -e`.
- If the audio can no longer be written (for example the disk fills up), Aku stops the recording by itself, tells you why, and still makes a transcript of what it has. It also refuses to start with less than 500 MB of disk space free.
- `aku doctor` checks that everything Aku needs is in place.

## Settings worth knowing

- **Default folder**: where finished transcripts go, for example a folder in your Obsidian vault. Until you finalise, the transcript lives only in Aku's own data folder.
- **Show speakers during the call**: on by default. It uses the built-in method and some extra CPU while recording; the full pass after the call still decides the final labels.
- **Hugging Face token**: optional. With a token (and the conditions accepted on the `pyannote/speaker-diarization-community-1` model page), speaker separation uses pyannote. Without one, Aku uses its built-in method, which needs no account.
- **OpenAI API key**: only for the cloud engine. Stored in the system keyring. With the cloud engine, call audio is sent to OpenAI; check your company's policy before using it on work calls.
- **Your voice**: a sample of your own voice, used only by interview mode to label your lines. It stays on this computer. **Forget my voice** removes it. If you switch between pyannote and the built-in method, record it again.
- **Saved voices**: voiceprints of people you chose to remember. They stay on this computer. **Forget** removes one.

## Where things are

| What | Where |
| --- | --- |
| Recordings and session data | `~/.local/share/aku/sessions/<id>/` |
| Models, whisper.cpp build | `~/.local/share/aku/models/`, `~/.local/share/aku/deps/` |
| Saved voices, vocabulary | `~/.local/share/aku/aku.db` |
| Settings | `~/.config/aku/settings.json` |
| Finished transcripts | the folder you chose (default `~/Documents/Aku`) |

## Commands

```
aku open            open the UI (starts the service if needed)
aku toggle          start with the last settings, or stop
aku status          what Aku is doing
aku devices         microphones, outputs and apps playing audio
aku record          the whole flow in the terminal, without the service (--interview for interview mode)
aku enrol           record your own voice once, so interview mode knows which speaker is you
aku bench FILE.wav  measure how fast each local model runs on this machine
aku doctor          check that everything Aku needs is in place
aku setup           set up the background service and launcher entry again (--remove to take them away)
aku serve           run the service in this terminal (the installer runs it for you)
```

## Development

```bash
cd backend && uv sync                 # Python environment
uv run aku serve --no-tray            # the service, in this terminal
cd frontend && npm ci && npm run dev   # UI with hot reload at http://localhost:5173
```

For the dev UI, start the service with `AKU_DEV_ORIGIN=http://localhost:5173` so it accepts requests from the Vite server.

Tests and checks. The `check_*` scripts play test recordings through silent virtual devices: nothing comes out of your speakers and your real microphone is not used.

```bash
cd backend
uv run pytest                                  # unit tests
uv run python ../scripts/make_test_audio.py    # once: builds test recordings in ~/.cache/aku-test
uv run python ../scripts/check_capture.py      # both tracks match the wall clock, also when an app drops out
uv run python ../scripts/check_live.py         # record, live transcript, speakers, Markdown on a simulated call
uv run python ../scripts/check_live.py --long  # 10 minutes of back-to-back speech
uv run python ../scripts/check_interview.py    # interview mode: 10 minutes, three people at one microphone
uv run python ../scripts/check_diarize.py      # speaker separation at several call lengths
uv run python ../scripts/check_language.py     # Danish chosen, Automatic, and a mixed Danish/English call
uv run python ../scripts/check_recovery.py     # kill mid-recording and recover; improve; voiceprints
uv run python ../scripts/check_failures.py     # the audio can no longer be written mid-recording ("disk full")
bash ../scripts/check_tray.sh                  # tray icon and `aku toggle`
uv run python ../scripts/check_shortcut.py     # global-shortcuts portal (add --bind to go through the desktop's dialog)
cd ../frontend && npm test                     # UI unit tests
npx playwright install chromium                # once
node e2e/check_ui.mjs                          # the full flow in a headless browser (service must be running)
node e2e/check_language.mjs                    # the language selector, on Danish speech
node e2e/check_interview.mjs                   # interview mode (against a throwaway service; see the top of the file)
```

Layout:

```
backend/aku/
  audio/      PipeWire device and app listing, capture, the session-clock writer
  asr/        chunker (VAD), whisper.cpp and OpenAI backends, live transcriber, benchmark
  diarize/    worker process (pyannote or built-in), live separation, word-to-speaker mapping, voiceprints
  vocab/      vocabulary sets, replacement rules
  sessions/   lifecycle, recovery, merge, Markdown export
  api/        HTTP + WebSocket API, localhost guard, serves the built UI
  tray.py     tray icon over D-Bus
  shortcuts.py  global hotkey through the desktop portal
  cli.py      the aku command
frontend/     Vue 3 + Vite + TypeScript UI; builds into backend/aku/static
scripts/      installers (get-aku.sh: from a release; install.sh: from source), release, whisper.cpp build, checks
```

## Credits

- [whisper.cpp](https://github.com/ggml-org/whisper.cpp) (MIT) for transcription.
- [pyannote.audio](https://github.com/pyannote/pyannote-audio) (MIT) and the WeSpeaker ResNet34 embedding model (CC BY 4.0) for telling speakers apart.
- [Silero VAD](https://github.com/snakers4/silero-vad) (MIT) for finding speech.
- Test recordings are built from [Mini LibriSpeech](https://www.openslr.org/31/) and [FLEURS](https://huggingface.co/datasets/google/fleurs) (both CC BY 4.0).

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md). Use synthetic, redacted examples in issues;
never attach real calls, transcripts, voiceprints, tokens or unredacted logs.
Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## License

AGPL-3.0-or-later; see [LICENSE](LICENSE), [NOTICE](NOTICE) and
[third-party attribution](docs/licensing.md). Complete source is included with
releases and available in this repository.
