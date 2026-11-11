"""A tiny thread-safe event bus. The API forwards events to WebSocket clients."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from typing import Callable

Subscriber = Callable[[dict], None]
log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)

        return unsubscribe

    def emit(self, type: str, **data) -> None:
        with self._lock:
            subs = list(self._subs)
        event = {"type": type, **data}
        if type == "error":  # whatever the user is shown as an error is also on record
            log.warning("%s%s", f"[{data['session']}] " if data.get("session") else "", data.get("message"))
        for fn in subs:
            try:
                fn(event)
            except Exception:  # noqa: BLE001 - one bad subscriber must not stop the rest
                log.exception("an event subscriber failed on a %s event", type)


def notify(title: str, body: str = "", enabled: bool = True) -> None:
    """Desktop notification (start/stop/ready). Never blocks, never raises."""
    if not enabled or not shutil.which("notify-send"):
        return
    try:
        subprocess.Popen(
            ["notify-send", "--app-name=Aku", "--icon=audio-input-microphone", title, body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        log.debug("notification not shown: %s", e)
