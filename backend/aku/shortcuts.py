"""Global hotkey through the XDG GlobalShortcuts portal (works on Wayland, GNOME 48+ and KDE).

The desktop owns the key: the first time, it shows a dialog where the user confirms or
changes the shortcut, and it remembers the choice for this app. Aku then gets an
"Activated" signal whenever the key is pressed.

Without the portal (older desktops), bind the command `aku toggle` to a key instead.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from typing import Callable

from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST = "org.freedesktop.portal.Request"
APP_ID = "aku"  # must match the installed launcher entry (aku.desktop, written by scripts/install.sh)
SHORTCUT = "toggle"


log = logging.getLogger(__name__)


class PortalError(RuntimeError):
    pass


class GlobalShortcut:
    def __init__(self, on_activate: Callable[[], None], trigger: str = "CTRL+ALT+r", app_id: str = APP_ID):
        self.on_activate = on_activate
        self.trigger = trigger
        self.app_id = app_id
        # state: off | binding (waiting for the user's confirmation) | bound | unavailable | error
        self.status: dict = {"state": "off", "trigger": None, "error": None}
        self._bus: MessageBus | None = None
        self._session: str | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._tokens = itertools.count(1)
        self._task: asyncio.Task | None = None

    # -- public -------------------------------------------------------------------

    def enable(self) -> None:
        """Start binding in the background (the desktop may show a dialog)."""
        if self._task is None or self._task.done():
            self.status = {"state": "binding", "trigger": None, "error": None}
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def disable(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        await self._close()
        self.status = {"state": "off", "trigger": None, "error": None}

    async def configure(self) -> None:
        """Ask the desktop to show its dialog for changing the key."""
        if not (self._bus and self._session):
            raise PortalError("The shortcut is not set up yet.")
        await self._call("ConfigureShortcuts", "osa{sv}", [self._session, "", {}])

    # -- portal conversation ---------------------------------------------------------

    async def _run(self) -> None:
        try:
            await self._connect()
            created = await self._request("CreateSession", "a{sv}", lambda token: [
                {"handle_token": Variant("s", token), "session_handle_token": Variant("s", "aku")}
            ], timeout=30)
            self._session = created["session_handle"].value
            wanted = [[SHORTCUT, {
                "description": Variant("s", "Aku: start or stop recording"),
                "preferred_trigger": Variant("s", self.trigger),
            }]]
            bound = await self._request("BindShortcuts", "oa(sa{sv})sa{sv}", lambda token: [
                self._session, wanted, "", {"handle_token": Variant("s", token)}
            ], timeout=600)  # the user may take a while in the dialog
            self.status = {"state": "bound", "trigger": self._describe(bound), "error": None}
            log.info("global shortcut bound: %s", self.status["trigger"])
        except asyncio.CancelledError:
            raise
        except PortalError as e:
            unavailable = "unavailable" in str(e)
            self.status = {"state": "unavailable" if unavailable else "error", "trigger": None, "error": str(e)}
            log.warning("global shortcut not set up: %s", e)
            await self._close()
        except Exception as e:  # noqa: BLE001 - never take the service down over a hotkey
            self.status = {"state": "error", "trigger": None, "error": f"{type(e).__name__}: {e}"}
            log.exception("global shortcut failed")
            await self._close()

    @staticmethod
    def _describe(results: dict) -> str | None:
        for entry in (results.get("shortcuts").value if results.get("shortcuts") else []):
            if entry[0] == SHORTCUT:
                desc = entry[1].get("trigger_description")
                return desc.value if desc else None
        return None

    async def _connect(self) -> None:
        self._bus = await MessageBus().connect()
        reply = await self._bus.call(Message(
            destination=BUS, path=PATH, interface="org.freedesktop.DBus.Properties", member="Get",
            signature="ss", body=[IFACE, "version"],
        ))
        if reply.message_type == MessageType.ERROR:
            raise PortalError("unavailable: this desktop has no global shortcuts portal")
        # Tell the portal which app this is, so the desktop can remember the shortcut (portal 1.20+).
        # Must come before any other portal call on this connection; older portals lack it.
        reply = await self._bus.call(Message(
            destination=BUS, path=PATH, interface="org.freedesktop.host.portal.Registry", member="Register",
            signature="sa{sv}", body=[self.app_id, {}],
        ))
        if reply.message_type == MessageType.ERROR and "Unknown" not in (reply.error_name or ""):
            raise PortalError(
                f"The desktop does not know the app '{self.app_id}'. Run scripts/install.sh so the launcher entry exists. "
                f"({reply.body[0] if reply.body else reply.error_name})"
            )
        for rule in (f"type='signal',sender='{BUS}',interface='{REQUEST}',member='Response'",
                     f"type='signal',sender='{BUS}',interface='{IFACE}',member='Activated'"):
            await self._bus.call(Message(
                destination="org.freedesktop.DBus", path="/org/freedesktop/DBus", interface="org.freedesktop.DBus",
                member="AddMatch", signature="s", body=[rule],
            ))
        self._bus.add_message_handler(self._on_message)

    def _on_message(self, msg: Message):
        if msg.message_type != MessageType.SIGNAL:
            return None
        if msg.interface == REQUEST and msg.member == "Response":
            future = self._pending.pop(msg.path, None)
            if future and not future.done():
                future.set_result(msg.body)
        elif msg.interface == IFACE and msg.member == "Activated":
            session, shortcut = msg.body[0], msg.body[1]
            if session == self._session and shortcut == SHORTCUT:
                self.on_activate()
        return None

    async def _call(self, member: str, signature: str, body: list) -> Message:
        reply = await self._bus.call(Message(destination=BUS, path=PATH, interface=IFACE, member=member, signature=signature, body=body))
        if reply.message_type == MessageType.ERROR:
            raise PortalError(f"{member} failed: {reply.body[0] if reply.body else reply.error_name}")
        return reply

    async def _request(self, member: str, signature: str, make_body, timeout: float) -> dict:
        """A portal request: call, then wait for the Response signal on the request object."""
        token = f"aku{next(self._tokens)}"
        sender = self._bus.unique_name[1:].replace(".", "_")
        path = f"{PATH}/request/{sender}/{token}"
        future = asyncio.get_running_loop().create_future()
        self._pending[path] = future
        reply = await self._call(member, signature, make_body(token))
        handle = reply.body[0]
        if handle != path:  # very old portals pick their own path
            self._pending[handle] = self._pending.pop(path)
        try:
            code, results = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as e:
            self._pending.pop(handle, None)
            raise PortalError(f"{member}: no answer from the desktop") from e
        if code == 1:
            raise PortalError("You cancelled the shortcut dialog.")
        if code != 0:
            raise PortalError(f"{member} was refused by the desktop.")
        return results

    async def _close(self) -> None:
        bus, self._bus = self._bus, None
        session, self._session = self._session, None
        if bus:
            try:
                if session:
                    await bus.call(Message(destination=BUS, path=session, interface="org.freedesktop.portal.Session", member="Close"))
            except Exception:  # noqa: BLE001
                pass
            bus.disconnect()
