"""Session lifecycle: record -> process -> name speakers -> finalise, plus recovery and the quality pass."""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import config, diarize
from ..asr.base import Backend
from ..asr.live import LanguagePicker, Segment, Transcriber, transcribe_file
from ..audio import pipewire, wav
from ..audio.capture import Capture
from ..config import SAMPLE_RATE as SR
from ..diarize import voiceprints
from ..events import EventBus, notify
from ..vocab import store as vocab
from . import markdown, merge
from .store import Session, all_sessions, new_session


class Busy(RuntimeError):
    """The request clashes with something already running."""


class NotFound(LookupError):
    pass


class Invalid(ValueError):
    pass


@dataclass
class Active:
    session: Session
    capture: Capture
    transcriber: Transcriber
    backend: Backend
    rules: list
    user_name: str
    next_id: int = 1
    stop_ticker: threading.Event = field(default_factory=threading.Event)
    mic_quiet: float = 0.0  # seconds of pure digital silence on the mic (hardware mute?)


@dataclass
class Job:
    session: str
    kind: str
    step: str = ""
    progress: float | None = None


def parse_speakers(value) -> int | None:
    """"auto" (or nothing) -> None; otherwise an exact speaker count."""
    if value in (None, "", "auto", 0, "0"):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError) as e:
        raise Invalid('speakers must be "auto" or a number') from e
    if not 1 <= n <= 20:
        raise Invalid("speakers must be between 1 and 20")
    return n


def parse_language(value) -> str:
    """A language code Aku offers, or "auto"."""
    code = str(value or "").strip().lower()
    if code not in config.LANGUAGES:
        raise Invalid(f"Unknown language {value!r}. Use a code like en or da, or auto.")
    return code


def prompt_maker(sets: list[str]):
    """language -> Whisper prompt for these vocabulary sets (looked up once per language)."""
    names = voiceprints.names()
    cache: dict[str, str] = {}

    def prompt_for(language: str) -> str:
        if language not in cache:
            cache[language] = vocab.build_prompt(sets, names, language)
        return cache[language]

    return prompt_for


def normalise_sources(sources: dict | None) -> dict:
    s = dict(config.DEFAULTS["last_sources"])
    s.update({k: v for k, v in (sources or {}).items() if k in s})
    s["apps"] = [str(a) for a in (s.get("apps") or [])]
    if s["scope"] not in ("all", "apps"):
        raise Invalid('scope must be "all" or "apps"')
    if s["scope"] == "apps" and not s["apps"]:
        raise Invalid("Pick at least one app, or record everything.")
    return s


class Manager:
    def __init__(self, bus: EventBus | None = None):
        self.bus = bus or EventBus()
        self.active: Active | None = None
        self.jobs: dict[str, Job] = {}
        self.download: dict | None = None
        self._lock = threading.RLock()
        self._heavy = threading.Lock()  # one GPU/CPU-heavy job at a time
        config.ensure_dirs()
        self.mark_interrupted()

    # -- helpers ---------------------------------------------------------------

    def _settings(self) -> dict:
        return config.load_settings()

    def _notify(self, title: str, body: str = "") -> None:
        notify(title, body, self._settings().get("notifications", True))

    def session(self, sid: str) -> Session:
        s = Session(sid)
        if "/" in sid or ".." in sid or not s.exists():
            raise NotFound(f"No session {sid}")
        return s

    def make_backend(self, kind: str, model: str | None = None, port_offset: int = 0) -> Backend:
        settings = self._settings()
        if kind == "cloud":
            from ..asr.cloud_openai import OpenAIBackend

            key = config.get_secret("openai_api_key")
            if not key:
                raise Invalid("No OpenAI API key set. Add one in Settings, or use the local backend.")
            return OpenAIBackend(key, model or settings["cloud_model"])
        from ..asr.whisper_cpp import WhisperCpp

        def progress(done: int, total: int) -> None:
            # a first-time model download: shown in the UI until it completes
            self.download = {"model": name, "progress": round(done / total, 3)} if total and done < total else None

        name = model or settings["local_model"]
        return WhisperCpp(name, settings["whisper_port"] + port_offset, settings["use_gpu"], on_progress=progress)

    def _emit_session(self, session: Session) -> None:
        self.bus.emit("session", session=self.summary(session))

    # -- status ------------------------------------------------------------------

    def status(self) -> dict:
        a = self.active
        out = {
            "recording": a is not None,
            "session": a.session.id if a else None,
            "elapsed": round(a.capture.elapsed(), 1) if a else 0,
            "levels": {n: round(t.level, 3) for n, t in a.capture.tracks.items()} if a else {},
            "pending": a.transcriber.pending if a else 0,
            "capture_errors": a.capture.errors() if a else {},
            "mic_silent": bool(a and a.mic_quiet >= 8),
            "download": self.download,
            "jobs": {sid: {"kind": j.kind, "step": j.step, "progress": j.progress} for sid, j in self.jobs.items()},
        }
        return out

    def devices(self) -> dict:
        return pipewire.list_devices()

    def summary(self, session: Session) -> dict:
        meta = session.meta
        job = self.jobs.get(session.id)
        return {
            "id": session.id,
            "title": meta.get("title") or "",
            "created": meta.get("created"),
            "duration": meta.get("duration") or 0,
            "status": meta.get("status"),
            "backend": meta.get("backend_name"),
            "improved_with": meta.get("improved_with"),
            "language": meta.get("language") or "en",
            "output_path": meta.get("output_path"),
            "has_audio": session.has_audio,
            "error": meta.get("error"),
            "job": {"kind": job.kind, "step": job.step, "progress": job.progress} if job else None,
        }

    def list_sessions(self) -> list[dict]:
        return [self.summary(s) for s in all_sessions()]

    def detail(self, sid: str) -> dict:
        session = self.session(sid)
        meta = session.meta
        user = meta.get("user_name") or "Me"
        speakers = session.speakers
        out = self.summary(session)
        out.update(
            sources=meta.get("sources"),
            vocab_sets=meta.get("vocab_sets") or [],
            speakers_requested=meta.get("speakers_requested") or "auto",
            diarizer=speakers.get("engine"),
            user_name=user,
        )
        if meta.get("status") in ("recording", "processing", "interrupted", "error") and not session.transcript_path.exists():
            rules = vocab.compile_rules()
            segs = sorted(session.segments(), key=lambda s: s.start)
            out["lines"] = [
                {"id": s.id, "start": round(s.start, 2), "end": round(s.end, 2),
                 "speaker": merge.ME if s.track == "mic" else merge.THEM,
                 "name": user if s.track == "mic" else "Them", "text": vocab.apply_rules(s.text, rules)}
                for s in segs
            ]
            out["speakers"] = []
        else:
            lines = session.transcript.get("lines", [])
            out["lines"] = [dict(l, name=merge.display_name(l["speaker"], speakers, user)) for l in lines]
            talk: dict[str, int] = {}
            for l in lines:
                talk[l["speaker"]] = talk.get(l["speaker"], 0) + 1
            out["speakers"] = [
                {"id": spk, "name": s.get("name"), "suggested": s.get("suggested"), "score": s.get("score"),
                 "duration": s.get("duration"), "clip": s.get("clip"), "remember": s.get("remember", False),
                 "saved": s.get("saved", False), "lines": talk.get(spk, 0),
                 "display": merge.display_name(spk, speakers, user)}
                for spk, s in speakers.get("speakers", {}).items()
            ]
        return out

    # -- recording -----------------------------------------------------------------

    def start(
        self,
        sources: dict | None = None,
        backend: str | None = None,
        vocab_sets: list[str] | None = None,
        language: str | None = None,
    ) -> dict:
        with self._lock:
            if self.active:
                raise Busy("Already recording.")
            settings = self._settings()
            sources = normalise_sources(sources if sources is not None else settings["last_sources"])
            kind = backend or settings["backend"]
            if kind not in ("local", "cloud"):
                raise Invalid('backend must be "local" or "cloud"')
            sets = settings["active_vocab_sets"] if vocab_sets is None else list(vocab_sets)
            language = parse_language(language or settings["language"])
            be = self.make_backend(kind)
            session = new_session()
            user = settings["user_name"] or "Me"
            session.update(
                id=session.id, created=datetime.now().astimezone().isoformat(timespec="seconds"),
                status="recording", title="", duration=0, sources=sources, backend=kind,
                backend_name=be.name, vocab_sets=sets, language=language, user_name=user,
            )
            config.save_settings({"last_sources": sources, "active_vocab_sets": sets, "backend": kind, "language": language})
            capture = Capture(session.dir, sources, tag=session.id[-6:])
            transcriber = Transcriber(
                be, on_segment=lambda seg: self._on_segment(session.id, seg), prompt=prompt_maker(sets),
                language=language, session_time=capture.elapsed,
                on_error=lambda msg: self.bus.emit("error", session=session.id, message=msg),
            )
            for name, track in capture.tracks.items():
                track.listeners.append(transcriber.chunker(name).feed)
            active = Active(session, capture, transcriber, be, vocab.compile_rules(), user)
            self.active = active
            capture.start()
            for msg in capture.warnings:
                self.bus.emit("error", session=session.id, message=msg)
        if hasattr(be, "start"):
            threading.Thread(target=self._warm_up, args=(be, session.id), daemon=True).start()
        threading.Thread(target=self._ticker, args=(active,), daemon=True).start()
        self._notify("Aku is recording", "Press the hotkey again to stop.")
        self._emit_session(session)
        self.bus.emit("status", **self.status())
        return self.summary(session)

    def _warm_up(self, backend, sid: str) -> None:
        try:
            backend.start()
        except Exception as e:  # noqa: BLE001
            self.bus.emit("error", session=sid, message=f"Local transcription could not start: {e}")

    def _ticker(self, active: Active) -> None:
        while not active.stop_ticker.wait(0.5):
            # a muted microphone delivers exact zeros; a live one always has some noise
            active.mic_quiet = active.mic_quiet + 0.5 if active.capture.mic.level == 0.0 else 0.0
            self.bus.emit("status", **self.status())

    def _on_segment(self, sid: str, seg: Segment) -> None:
        a = self.active
        session = Session(sid)
        with self._lock:
            if a and a.session.id == sid:
                seg.id = a.next_id
                a.next_id += 1
                rules, user = a.rules, a.user_name
            else:  # the tail of a session that just stopped
                seg.id = max((s.id for s in session.segments()), default=0) + 1
                rules, user = vocab.compile_rules(), session.meta.get("user_name") or "Me"
        session.append_segment(seg)
        text = vocab.apply_rules(seg.text, rules)
        name = user if seg.track == "mic" else "Them"
        with open(session.temp_md, "a") as f:
            f.write(markdown.line_md(seg.start, name, text) + "\n\n")
        self.bus.emit(
            "segment", session=sid,
            segment={"id": seg.id, "start": round(seg.start, 2), "end": round(seg.end, 2),
                     "speaker": merge.ME if seg.track == "mic" else merge.THEM, "name": name,
                     "text": text, "lag": seg.lag},
        )

    def stop(self, sid: str | None = None, speakers="auto") -> dict:
        count = parse_speakers(speakers)
        with self._lock:
            a = self.active
            if a is None or (sid and sid != a.session.id):
                raise Invalid("That session is not recording.")
            self.active = None
        a.stop_ticker.set()
        duration = a.capture.stop()
        a.session.update(status="processing", duration=round(duration, 2), speakers_requested=count or "auto")
        self._notify("Aku stopped recording", "Finishing the transcript…")
        self.bus.emit("status", **self.status())
        self._emit_session(a.session)

        def work(job: Job) -> None:
            self._step(job, "Finishing transcription")
            a.transcriber.finish()
            a.backend.close()
            lags = a.transcriber.lags
            if lags:
                a.session.update(lag={"median": round(float(np.median(lags)), 2), "max": round(max(lags), 2), "count": len(lags)})
            self._diarize_and_merge(a.session, count, job)
            a.session.update(status="naming", error=None)
            self._notify("Transcript ready", "Open Aku to name the speakers.")

        self._run_job(a.session, "finish", work, on_error_status="error")
        return self.summary(a.session)

    def toggle(self) -> dict:
        if self.active:
            return {"action": "stopped", "session": self.stop()}
        return {"action": "started", "session": self.start()}

    # -- jobs ------------------------------------------------------------------------

    def _step(self, job: Job, step: str, progress: float | None = None) -> None:
        job.step, job.progress = step, progress
        self.bus.emit("status", **self.status())

    def _run_job(self, session: Session, kind: str, work, on_error_status: str | None = None) -> None:
        with self._lock:
            if session.id in self.jobs:
                raise Busy("This session is already being processed.")
            job = Job(session.id, kind, "Waiting")
            self.jobs[session.id] = job

        def runner() -> None:
            try:
                with self._heavy:
                    work(job)
            except Exception as e:  # noqa: BLE001 - shown to the user, session stays usable
                changes = {"error": str(e)}
                if on_error_status:
                    changes["status"] = on_error_status
                session.update(**changes)
                self.bus.emit("error", session=session.id, message=str(e))
            finally:
                with self._lock:
                    self.jobs.pop(session.id, None)
                self._emit_session(session)
                self.bus.emit("status", **self.status())

        threading.Thread(target=runner, daemon=True, name=f"aku-job-{kind}").start()

    def wait(self, sid: str, timeout: float = 3600) -> None:
        """Block until the session has no running job (CLI and tests)."""
        end = time.monotonic() + timeout
        while sid in self.jobs and time.monotonic() < end:
            time.sleep(0.1)

    # -- diarization + merge -----------------------------------------------------------

    def _diarize_and_merge(self, session: Session, speakers: int | None, job: Job) -> None:
        has_them = any(s.track == "system" for s in session.segments())
        if has_them and session.system.exists():
            old = session.diarization
            old_speakers = session.speakers.get("speakers", {})
            previous = None
            if old:
                previous = {
                    "turns": old.get("exclusive") or [],
                    "named": {s["label"]: {"name": s.get("name"), "remember": s.get("remember", False)}
                              for s in old_speakers.values() if s.get("name")},
                }

            def progress(p: float, step: str) -> None:
                job.step, job.progress = step or "Separating speakers", p
                self.bus.emit("diarization_progress", session=session.id, progress=round(p, 3), step=job.step)

            self._step(job, "Separating speakers", 0.0)
            engine = diarize.choose_engine(self._settings()["diarizer"])
            started = time.monotonic()
            try:
                result = diarize.run(session.system, session.diarization_path, speakers, engine, progress)
            except diarize.DiarizationError as e:
                if engine != "pyannote":
                    raise
                self.bus.emit("error", session=session.id,
                              message=f"pyannote failed ({e}). Used the built-in speaker separation instead.")
                result = diarize.run(session.system, session.diarization_path, speakers, "basic", progress)
            session.update(diarization_seconds=round(time.monotonic() - started, 1))
            session.save_speakers(merge.build_speakers(result, previous))
        else:
            session.diarization_path.unlink(missing_ok=True)
            session.save_speakers({"speakers": {}})
        self._step(job, "Building transcript")
        self._rebuild(session)

    def _rebuild(self, session: Session, keep_previous: bool = False) -> None:
        lines = merge.build_lines(session.segments(), session.diarization, session.speakers)
        session.save_transcript({"lines": lines}, keep_previous)
        if session.meta.get("status") == "finalised":
            self._export(session)
        else:  # until finalised, the Markdown lives only in the session folder
            session.temp_md.write_text(markdown.render(session.meta, lines, session.speakers))

    def rediarize(self, sid: str, speakers="auto") -> dict:
        session = self.session(sid)
        count = parse_speakers(speakers)
        if session.meta.get("status") not in ("naming", "finalised"):
            raise Invalid("This session is not ready for that yet.")
        if not session.system.exists():
            raise Invalid("The audio for this session was deleted.")
        session.update(speakers_requested=count or "auto")
        self._run_job(session, "diarize", lambda job: self._diarize_and_merge(session, count, job))
        return self.summary(session)

    # -- naming + finalise ----------------------------------------------------------------

    def set_speaker(self, sid: str, spk: str, name: str | None, remember: bool | None = None) -> dict:
        session = self.session(sid)
        data = session.speakers
        s = data.get("speakers", {}).get(spk)
        if s is None:
            raise NotFound(f"No speaker {spk}")
        if name is not None:
            s["name"] = " ".join(name.split()) or None
        if remember is not None:
            s["remember"] = bool(remember)
        session.save_speakers(data)
        if session.meta.get("status") == "finalised":
            self._save_voiceprints(session)
            self._export(session)
        self._emit_session(session)
        return self.detail(sid)

    def _save_voiceprints(self, session: Session) -> None:
        """Only for speakers the user named and ticked "Remember this voice"."""
        data = session.speakers
        diar = session.diarization or {}
        embeddings = {s["label"]: s.get("embedding") for s in diar.get("speakers", [])}
        changed = False
        for s in data.get("speakers", {}).values():
            name = s.get("name")
            if s.get("remember") and name and s.get("saved_as") != name and embeddings.get(s["label"]):
                voiceprints.add(name, embeddings[s["label"]], data.get("space", ""), session.id)
                s["saved"], s["saved_as"] = True, name
                changed = True
        if changed:
            session.save_speakers(data)

    def _export(self, session: Session, folder: Path | None = None) -> Path:
        meta = session.meta
        old = Path(meta["output_path"]) if meta.get("output_path") else None
        folder = folder or (old.parent if old else Path(self._settings()["output_folder"]).expanduser())
        folder.mkdir(parents=True, exist_ok=True)
        path = markdown.output_path(folder, meta, current=old)
        text = markdown.render(meta, session.transcript.get("lines", []), session.speakers)
        if old and old.exists():
            shutil.copy2(old, session.dir / "export.prev.md")  # previous version stays in the session folder
            if old != path:
                old.unlink()
        path.write_text(text)
        session.update(output_path=str(path))
        return path

    def finalize(self, sid: str, title: str | None = None, output_folder: str | None = None) -> dict:
        session = self.session(sid)
        if session.meta.get("status") not in ("naming", "finalised"):
            raise Invalid("This session is not ready to finalise yet.")
        if sid in self.jobs:
            raise Busy("This session is still being processed.")
        if title is not None:
            session.update(title=" ".join(title.split()))
        folder = Path(output_folder).expanduser() if output_folder else None
        if folder is not None and not folder.is_absolute():
            raise Invalid("The output folder must be a full path.")
        self._save_voiceprints(session)
        try:
            self._export(session, folder)
        except OSError as e:
            raise Invalid(f"Could not write the transcript: {e}") from e
        session.temp_md.unlink(missing_ok=True)
        session.update(status="finalised", error=None)
        self._emit_session(session)
        return self.detail(sid)

    # -- quality pass ------------------------------------------------------------------------

    def improve(self, sid: str, engine: str) -> dict:
        session = self.session(sid)
        if engine not in ("local_large", "cloud"):
            raise Invalid('engine must be "local_large" or "cloud"')
        if session.meta.get("status") not in ("naming", "finalised"):
            raise Invalid("This session is not ready for that yet.")
        if not session.has_audio:
            raise Invalid("The audio for this session was deleted, so it cannot be re-transcribed.")
        if self.active:
            raise Busy("Stop the current recording first.")
        settings = self._settings()
        backend = self.make_backend("cloud") if engine == "cloud" else self.make_backend("local", settings["improve_model"], 1)

        def work(job: Job) -> None:
            meta = session.meta
            prompt = prompt_maker(meta.get("vocab_sets") or [])
            picker = LanguagePicker(meta.get("language") or "en")  # one for both tracks
            new: list[Segment] = []
            total = sum(wav.frames(session.track(t)) for t in ("mic", "system")) or 1
            done = 0
            errors: list[str] = []
            try:
                self._step(job, "Loading model")
                if hasattr(backend, "start"):
                    backend.on_progress = lambda got, size: self._step(job, f"Downloading model {backend.model}", got / size if size else None)
                    backend.start()
                for track in ("mic", "system"):
                    size = wav.frames(session.track(track))
                    transcribe_file(
                        session.track(track), track, backend, new.append, prompt, picker,
                        progress=lambda p, d=done, n=size: self._step(job, "Re-transcribing", (d + p * n) / total),
                        on_error=errors.append,
                    )
                    done += size
            finally:
                backend.close()
            if errors and not new:
                raise RuntimeError(errors[0])
            new.sort(key=lambda s: s.start)
            for i, seg in enumerate(new, start=1):
                seg.id = i
            shutil.copy2(session.segments_path, session.dir / "segments.prev.jsonl")
            session.segments_path.write_text("")
            for seg in new:
                session.append_segment(seg)
            session.update(improved_with=backend.name, error=errors[0] if errors else None)
            self._step(job, "Building transcript")
            self._rebuild(session, keep_previous=True)  # names and diarization are kept, mapped onto the new text

        self._run_job(session, "improve", work)
        return self.summary(session)

    # -- vocabulary fix ---------------------------------------------------------------------------

    def fix(self, wrong: str, right: str, set_name: str | None = None, sid: str | None = None) -> dict:
        rule = vocab.add_rule(wrong, right, set_name)
        if self.active:
            self.active.rules = vocab.compile_rules()
        if sid:
            session = self.session(sid)
            if session.transcript_path.exists():
                self._rebuild(session)
        return rule

    def rules_changed(self) -> None:
        if self.active:
            self.active.rules = vocab.compile_rules()

    # -- audio + deletion ------------------------------------------------------------------------

    def clip(self, sid: str, start: float, end: float, track: str = "system") -> bytes:
        session = self.session(sid)
        if track not in ("mic", "system", "mix"):
            raise Invalid("track must be mic, system or mix")
        if not session.has_audio:
            raise NotFound("The audio for this session was deleted.")
        a, n = max(0, int(start * SR)), max(0, int((min(end, start + 120) - start) * SR))
        if track == "mix":
            mic, system = wav.read(session.mic, a, n), wav.read(session.system, a, n)
            m = min(len(mic), len(system))
            pcm = np.clip(mic[:m].astype(np.int32) + system[:m].astype(np.int32), -32768, 32767).astype(np.int16)
        else:
            pcm = wav.read(session.track(track), a, n)
        return wav.encode(pcm)

    def speaker_clip(self, sid: str, spk: str) -> bytes:
        s = self.session(sid).speakers.get("speakers", {}).get(spk)
        if s is None:
            raise NotFound(f"No speaker {spk}")
        return self.clip(sid, s["clip"][0], s["clip"][1], "system")

    def delete_audio(self, sid: str) -> dict:
        session = self.session(sid)
        if (self.active and self.active.session.id == sid) or sid in self.jobs:
            raise Busy("This session is still being recorded or processed.")
        for p in (session.mic, session.system):
            p.unlink(missing_ok=True)
        session.update(audio_deleted=True)
        self._emit_session(session)
        return self.summary(session)

    def delete_session(self, sid: str) -> None:
        session = self.session(sid)
        if (self.active and self.active.session.id == sid) or sid in self.jobs:
            raise Busy("This session is still being recorded or processed.")
        session.delete()
        self.bus.emit("session", session={"id": sid, "deleted": True})

    # -- crash recovery -----------------------------------------------------------------------------

    def mark_interrupted(self) -> list[str]:
        """On launch: sessions left mid-recording or mid-processing by a crash."""
        out = []
        for s in all_sessions():
            if s.meta.get("status") in ("recording", "processing"):
                s.update(status="interrupted")
                out.append(s.id)
        return out

    def recover(self, sid: str) -> dict:
        session = self.session(sid)
        if session.meta.get("status") not in ("interrupted", "error"):
            raise Invalid("This session does not need recovering.")
        if not session.has_audio:
            raise Invalid("The audio for this session is missing, so it cannot be recovered.")
        if self.active:
            raise Busy("Stop the current recording first.")
        meta = session.meta
        backend = self.make_backend(meta.get("backend") or "local")
        count = parse_speakers(meta.get("speakers_requested"))

        def work(job: Job) -> None:
            self._step(job, "Repairing audio")
            sizes = {t: wav.repair(session.track(t)) for t in ("mic", "system")}
            longest = max(sizes.values())
            for t, n in sizes.items():  # both tracks end at the same moment
                if n < longest:
                    with open(session.track(t), "ab") as f:
                        f.write(np.zeros(longest - n, dtype="<i2").tobytes())
                    wav.repair(session.track(t))
            session.update(duration=round(longest / SR, 2), status="processing")
            existing = session.segments()
            next_id = max((s.id for s in existing), default=0) + 1
            prompt = prompt_maker(meta.get("vocab_sets") or [])
            picker = LanguagePicker(meta.get("language") or "en")

            def add(seg: Segment) -> None:
                nonlocal next_id
                seg.id = next_id
                next_id += 1
                session.append_segment(seg)

            errors: list[str] = []
            try:
                if hasattr(backend, "start"):
                    self._step(job, "Loading model")
                    backend.start()
                for i, track in enumerate(("mic", "system")):
                    resume = max((s.chunk_end for s in existing if s.track == track), default=0.0)
                    transcribe_file(
                        session.track(track), track, backend, add, prompt, picker,
                        start_sample=int(resume * SR),
                        progress=lambda p, i=i: self._step(job, "Finishing transcription", (i + p) / 2),
                        on_error=errors.append,
                    )
            finally:
                backend.close()
            self._diarize_and_merge(session, count, job)
            session.update(status="naming", error=errors[0] if errors else None)
            self._notify("Recording recovered", "Open Aku to name the speakers.")

        self._run_job(session, "recover", work, on_error_status="error")
        return self.summary(session)

    def shutdown(self) -> None:
        """Service is exiting: stop a running recording cleanly (it stays recoverable)."""
        a = self.active
        if a:
            a.stop_ticker.set()
            duration = a.capture.stop()
            a.session.update(status="interrupted", duration=round(duration, 2))
            self.active = None
            try:
                a.backend.close()
            except Exception:  # noqa: BLE001
                pass
