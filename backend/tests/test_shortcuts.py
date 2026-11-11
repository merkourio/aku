import asyncio
from types import SimpleNamespace

import pytest
from dbus_fast import MessageType, Variant

from aku import shortcuts
from aku.shortcuts import BUS, IFACE, PATH, REQUEST, GlobalShortcut


class FakeBus:
    """Plays the portal's side of the conversation."""

    unique_name = ":1.77"

    def __init__(self, bind_code=0, has_portal=True):
        self.calls = []
        self.handler = None
        self.bind_code = bind_code
        self.has_portal = has_portal
        self.disconnected = False

    async def connect(self):
        return self

    def add_message_handler(self, handler):
        self.handler = handler

    def disconnect(self):
        self.disconnected = True

    def _signal(self, path, interface, member, body):
        self.handler(SimpleNamespace(path=path, interface=interface, member=member, message_type=MessageType.SIGNAL, body=body))

    async def call(self, msg):
        self.calls.append((msg.interface, msg.member, msg.body))
        reply = SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[], error_name=None)
        if msg.member == "Get" and not self.has_portal:
            return SimpleNamespace(message_type=MessageType.ERROR, error_name="org.freedesktop.DBus.Error.UnknownInterface", body=["no"])
        if msg.interface == IFACE and msg.member in ("CreateSession", "BindShortcuts"):
            token = (msg.body[0] if msg.member == "CreateSession" else msg.body[3])["handle_token"].value
            request = f"{PATH}/request/1_77/{token}"
            reply.body = [request]
            if msg.member == "CreateSession":
                results = {"session_handle": Variant("s", f"{PATH}/session/1_77/aku")}
                asyncio.get_running_loop().call_soon(self._signal, request, REQUEST, "Response", [0, results])
            else:
                shortcuts_out = [["toggle", {"trigger_description": Variant("s", "Ctrl+Alt+R")}]]
                results = {"shortcuts": Variant("a(sa{sv})", shortcuts_out)}
                asyncio.get_running_loop().call_soon(self._signal, request, REQUEST, "Response", [self.bind_code, results])
        return reply


def use(monkeypatch, bus):
    monkeypatch.setattr(shortcuts, "MessageBus", lambda: bus)


async def settle(g):
    for _ in range(100):
        if g.status["state"] != "binding":
            return
        await asyncio.sleep(0.01)


async def test_binds_and_reacts_to_the_key(monkeypatch):
    bus = FakeBus()
    use(monkeypatch, bus)
    pressed = []
    g = GlobalShortcut(lambda: pressed.append(1), trigger="CTRL+ALT+r")
    g.enable()
    await settle(g)
    assert g.status == {"state": "bound", "trigger": "Ctrl+Alt+R", "error": None}
    members = [m for _, m, _ in bus.calls]
    assert members.index("Register") < members.index("CreateSession") < members.index("BindShortcuts")  # app id first
    bind = next(body for _, m, body in bus.calls if m == "BindShortcuts")
    assert bind[1][0][0] == "toggle" and bind[1][0][1]["preferred_trigger"].value == "CTRL+ALT+r"

    session = f"{PATH}/session/1_77/aku"
    bus._signal(PATH, IFACE, "Activated", [session, "toggle", 1, {}])
    bus._signal(PATH, IFACE, "Activated", [session, "something-else", 2, {}])
    bus._signal(PATH, IFACE, "Activated", ["/another/session", "toggle", 3, {}])
    assert pressed == [1]  # only our shortcut in our session

    await g.disable()
    assert g.status["state"] == "off" and bus.disconnected


async def test_cancelled_dialog_is_reported(monkeypatch):
    bus = FakeBus(bind_code=1)
    use(monkeypatch, bus)
    g = GlobalShortcut(lambda: None)
    g.enable()
    await settle(g)
    assert g.status["state"] == "error" and "cancelled" in g.status["error"]


async def test_desktop_without_the_portal(monkeypatch):
    use(monkeypatch, FakeBus(has_portal=False))
    g = GlobalShortcut(lambda: None)
    g.enable()
    await settle(g)
    assert g.status["state"] == "unavailable"
    with pytest.raises(shortcuts.PortalError):
        await g.configure()
