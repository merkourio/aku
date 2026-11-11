"""Local API: HTTP + WebSocket, and the built UI. Bound to 127.0.0.1 only."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, config
from ..diarize import choose_engine, voiceprints
from ..sessions.manager import Busy, Invalid, Manager, NotFound, parse_language
from ..vocab import store as vocab
from .security import LocalOnly

STATIC = Path(__file__).resolve().parent.parent / "static"
KNOWN_MODELS = ["large-v3-turbo", "large-v3", "medium", "small"]


class StartBody(BaseModel):
    input: str | None = None
    output: str | None = None
    scope: str | None = None
    apps: list[str] | None = None
    backend: str | None = None
    vocab_sets: list[str] | None = None
    language: str | None = None


class SpeakersBody(BaseModel):
    speakers: Any = "auto"


class SpeakerBody(BaseModel):
    name: str | None = None
    remember_voice: bool | None = None


class FinalizeBody(BaseModel):
    title: str | None = None
    output_folder: str | None = None


class ImproveBody(BaseModel):
    engine: str


class VocabBody(BaseModel):
    sets: list[dict] = []
    rules: list[dict] = []


class RuleBody(BaseModel):
    wrong: str
    right: str
    set: str | None = None
    session: str | None = None


def settings_view() -> dict:
    s = config.load_settings()
    s["has_openai_key"] = bool(config.get_secret("openai_api_key"))
    s["has_hf_token"] = bool(config.get_secret("hf_token"))
    s["diarizer_in_use"] = choose_engine(s["diarizer"])
    s["models"] = {m: config.model_path(m).exists() for m in KNOWN_MODELS}
    s["whisper_ready"] = config.whisper_bin("whisper-server") is not None
    s["languages"] = [{"code": code, "name": name} for code, name in config.LANGUAGES.items()]
    s["version"] = __version__
    return s


def create_app(manager: Manager | None = None, port: int | None = None, tray: bool = False) -> FastAPI:
    port = port or config.load_settings()["port"]
    manager = manager or Manager()
    clients: set[asyncio.Queue] = set()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()

        def forward(event: dict) -> None:
            def put() -> None:
                for q in list(clients):
                    if q.qsize() > 500:  # a stuck client: drop the oldest
                        with contextlib.suppress(asyncio.QueueEmpty):
                            q.get_nowait()
                    q.put_nowait(event)
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(put)

        unsubscribe = manager.bus.subscribe(forward)
        tray_task = None
        if tray:
            from ..tray import run_tray

            tray_task = asyncio.create_task(run_tray(manager, f"http://127.0.0.1:{port}/"))
        try:
            yield
        finally:
            unsubscribe()
            if tray_task:
                tray_task.cancel()
            await asyncio.to_thread(manager.shutdown)

    app = FastAPI(title="Aku", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalOnly, port=port)
    app.state.manager = manager

    @app.exception_handler(Busy)
    async def _busy(request: Request, exc: Busy):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(NotFound)
    async def _missing(request: Request, exc: NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)  # includes Invalid
    async def _invalid(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    # -- devices, status ---------------------------------------------------------

    @app.get("/devices")
    def devices():
        return manager.devices()

    @app.get("/status")
    def status():
        return manager.status()

    @app.post("/toggle")
    def toggle():
        return manager.toggle()

    # -- sessions ------------------------------------------------------------------

    @app.post("/sessions")
    def start(body: StartBody):
        sources = None
        given = {k: v for k, v in body.model_dump().items() if k in ("input", "output", "scope", "apps") and v is not None}
        if given or body.model_fields_set & {"input", "output"}:
            sources = dict(config.load_settings()["last_sources"])
            sources.update(given)
            for key in ("input", "output"):  # explicit null means "default device"
                if key in body.model_fields_set and getattr(body, key) is None:
                    sources[key] = None
        return manager.start(sources, body.backend, body.vocab_sets, body.language)

    @app.get("/sessions")
    def sessions():
        return manager.list_sessions()

    @app.get("/sessions/{sid}")
    def session(sid: str):
        return manager.detail(sid)

    @app.post("/sessions/{sid}/stop")
    def stop(sid: str, body: SpeakersBody | None = None):
        return manager.stop(sid, body.speakers if body else "auto")

    @app.post("/sessions/{sid}/diarize")
    def rediarize(sid: str, body: SpeakersBody | None = None):
        return manager.rediarize(sid, body.speakers if body else "auto")

    @app.get("/sessions/{sid}/speakers")
    def speakers(sid: str):
        return manager.detail(sid)["speakers"]

    @app.get("/sessions/{sid}/speakers/{spk}/clip")
    def speaker_clip(sid: str, spk: str):
        return Response(manager.speaker_clip(sid, spk), media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.put("/sessions/{sid}/speakers/{spk}")
    def set_speaker(sid: str, spk: str, body: SpeakerBody):
        return manager.set_speaker(sid, spk, body.name, body.remember_voice)

    @app.post("/sessions/{sid}/finalize")
    def finalize(sid: str, body: FinalizeBody | None = None):
        body = body or FinalizeBody()
        return manager.finalize(sid, body.title, body.output_folder)

    @app.post("/sessions/{sid}/improve")
    def improve(sid: str, body: ImproveBody):
        return manager.improve(sid, body.engine)

    @app.post("/sessions/{sid}/recover")
    def recover(sid: str):
        return manager.recover(sid)

    @app.get("/sessions/{sid}/audio")
    def audio(sid: str, start: float = 0.0, end: float = 10.0, track: str = "mix"):
        return Response(manager.clip(sid, start, end, track), media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.delete("/sessions/{sid}/audio")
    def delete_audio(sid: str):
        return manager.delete_audio(sid)

    @app.delete("/sessions/{sid}")
    def delete_session(sid: str):
        manager.delete_session(sid)
        return {"deleted": sid}

    # -- settings ---------------------------------------------------------------------

    @app.get("/settings")
    def get_settings():
        return settings_view()

    @app.put("/settings")
    def put_settings(body: dict):
        for secret in ("openai_api_key", "hf_token"):
            if secret in body:
                try:
                    config.set_secret(secret, (body.pop(secret) or "").strip() or None)
                except Exception as e:  # noqa: BLE001 - no keyring service running
                    raise HTTPException(500, f"Could not store the key in the system keyring: {e}") from e
        if "backend" in body and body["backend"] not in ("local", "cloud"):
            raise Invalid('backend must be "local" or "cloud"')
        if "diarizer" in body and body["diarizer"] not in ("auto", "pyannote", "basic"):
            raise Invalid("unknown diarizer")
        if "language" in body:
            body["language"] = parse_language(body["language"])
        if "output_folder" in body:
            folder = Path(str(body["output_folder"])).expanduser()
            if not folder.is_absolute():
                raise Invalid("The output folder must be a full path.")
            body["output_folder"] = str(folder)
        config.save_settings(body)
        return settings_view()

    # -- vocabulary ----------------------------------------------------------------------

    @app.get("/vocab")
    def get_vocab():
        return {"sets": vocab.list_sets(), "rules": vocab.list_rules()}

    @app.put("/vocab")
    def put_vocab(body: VocabBody):
        vocab.replace_all(body.sets, body.rules)
        names = {s["name"] for s in vocab.list_sets()}
        active = [s for s in config.load_settings()["active_vocab_sets"] if s in names]
        config.save_settings({"active_vocab_sets": active})
        manager.rules_changed()
        return get_vocab()

    @app.post("/vocab/replace-rule")
    def add_rule(body: RuleBody):
        rule = manager.fix(body.wrong, body.right, body.set, body.session)
        return {"rule": rule, **get_vocab()}

    # -- voiceprints -----------------------------------------------------------------------

    @app.get("/voiceprints")
    def get_voiceprints():
        return voiceprints.people()

    @app.delete("/voiceprints/{name}")
    def forget_voice(name: str):
        return {"forgotten": voiceprints.forget(name)}

    # -- live events -------------------------------------------------------------------------

    @app.websocket("/live")
    async def live(ws: WebSocket):
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue()
        clients.add(q)
        try:
            await ws.send_json({"type": "status", **manager.status()})
            while True:
                await ws.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            clients.discard(q)

    # -- the UI ---------------------------------------------------------------------------------

    if (STATIC / "assets").exists():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/", response_class=HTMLResponse)
    def index():
        page = STATIC / "index.html"
        if page.exists():
            return FileResponse(page, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(
            "<h1>Aku</h1><p>The service is running, but the UI has not been built yet. "
            "Run <code>npm run build</code> in <code>frontend/</code>.</p>"
        )

    @app.get("/favicon.svg")
    def favicon():
        icon = STATIC / "favicon.svg"
        if not icon.exists():
            raise HTTPException(404)
        return FileResponse(icon)

    return app
