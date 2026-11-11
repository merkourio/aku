"""Localhost-only guard.

The UI is a web page on 127.0.0.1, so any website open in the same browser could
try to call this API. Three checks stop that:

- Host must be localhost (blocks DNS rebinding).
- State-changing requests and WebSockets from a browser must come from our own origin.
- Browser requests marked cross-site are refused outright, also plain GETs (audio clips).
"""

from __future__ import annotations

import os

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def allowed_hosts(port: int) -> set[str]:
    names = ("127.0.0.1", "localhost", "[::1]")
    return {f"{n}:{port}" for n in names} | (set(names) if port == 80 else set())


def allowed_origins(port: int) -> set[str]:
    origins = {f"http://{n}:{port}" for n in ("127.0.0.1", "localhost", "[::1]")}
    dev = os.environ.get("AKU_DEV_ORIGIN")  # the Vite dev server, e.g. http://localhost:5173
    if dev:
        origins.add(dev.rstrip("/"))
    return origins


def check(scope_type: str, method: str, headers: dict[str, str], port: int) -> str | None:
    """Returns a reason to refuse the request, or None."""
    host = headers.get("host", "")
    if host not in allowed_hosts(port):
        return "bad host"
    origin = headers.get("origin")
    site = headers.get("sec-fetch-site")
    ours = origin in allowed_origins(port) if origin else False
    if site == "cross-site" and not ours:
        return "cross-site request"
    if scope_type == "websocket" or method not in SAFE_METHODS:
        if origin is not None and not ours:
            return "bad origin"
        if site is not None and site not in ("same-origin", "none") and not ours:
            return "bad origin"
    return None


class LocalOnly:
    def __init__(self, app: ASGIApp, port: int):
        self.app = app
        self.port = port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        reason = check(scope["type"], scope.get("method", "GET"), headers, self.port)
        if reason is None:
            await self.app(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await PlainTextResponse(f"Refused: {reason}", status_code=403)(scope, receive, send)
