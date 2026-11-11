"""Tray icon: a StatusNotifierItem spoken directly over D-Bus (no GTK needed).

KDE shows it out of the box; GNOME needs the AppIndicator extension. If no tray
host is present this quietly does nothing: hotkey, notifications and the
browser tab are enough.

No `from __future__ import annotations` here: dbus-fast reads the D-Bus
signatures from the annotations.
"""

import asyncio
import logging
import os
import signal as os_signal
import subprocess
import threading

from dbus_fast import PropertyAccess, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, dbus_property, method, signal

log = logging.getLogger(__name__)
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
ICON_IDLE = "audio-input-microphone-symbolic"
ICON_RECORDING = "media-record-symbolic"

TOGGLE, OPEN, QUIT = 1, 2, 4


class Menu(ServiceInterface):
    def __init__(self, tray: "Tray"):
        super().__init__("com.canonical.dbusmenu")
        self.tray = tray
        self.revision = 1

    def _props(self, item: int) -> dict:
        if item == TOGGLE:
            return {"label": Variant("s", "Stop recording" if self.tray.recording else "Start recording")}
        if item == OPEN:
            return {"label": Variant("s", "Open Aku")}
        if item == 3:
            return {"type": Variant("s", "separator")}
        if item == QUIT:
            return {"label": Variant("s", "Quit Aku")}
        return {"children-display": Variant("s", "submenu")}

    def _layout(self, item: int) -> list:
        children = [Variant("(ia{sv}av)", self._layout(i)) for i in (TOGGLE, OPEN, 3, QUIT)] if item == 0 else []
        return [item, self._props(item), children]

    @method()
    def GetLayout(self, parent_id: "i", recursion_depth: "i", property_names: "as") -> "u(ia{sv}av)":
        return [self.revision, self._layout(parent_id)]

    @method()
    def GetGroupProperties(self, ids: "ai", property_names: "as") -> "a(ia{sv})":
        return [[i, self._props(i)] for i in (ids or [0, TOGGLE, OPEN, 3, QUIT])]

    @method()
    def GetProperty(self, id: "i", name: "s") -> "v":
        return self._props(id).get(name, Variant("s", ""))

    @method()
    def Event(self, id: "i", event_id: "s", data: "v", timestamp: "u"):
        if event_id == "clicked":
            self.tray.clicked(id)

    @method()
    def EventGroup(self, events: "a(isvu)") -> "ai":
        for id, event_id, _data, _ts in events:
            if event_id == "clicked":
                self.tray.clicked(id)
        return []

    @method()
    def AboutToShow(self, id: "i") -> "b":
        return False

    @method()
    def AboutToShowGroup(self, ids: "ai") -> "aiai":
        return [[], []]

    @signal()
    def LayoutUpdated(self, revision: "u", parent: "i") -> "ui":
        return [revision, parent]

    @signal()
    def ItemsPropertiesUpdated(self, updated: "a(ia{sv})", removed: "a(ias)") -> "a(ia{sv})a(ias)":
        return [updated, removed]

    @dbus_property(access=PropertyAccess.READ)
    def Version(self) -> "u":
        return 3

    @dbus_property(access=PropertyAccess.READ)
    def TextDirection(self) -> "s":
        return "ltr"

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> "s":
        return "normal"

    @dbus_property(access=PropertyAccess.READ)
    def IconThemePath(self) -> "as":
        return []


class Item(ServiceInterface):
    def __init__(self, tray: "Tray"):
        super().__init__("org.kde.StatusNotifierItem")
        self.tray = tray

    @dbus_property(access=PropertyAccess.READ)
    def Category(self) -> "s":
        return "ApplicationStatus"

    @dbus_property(access=PropertyAccess.READ)
    def Id(self) -> "s":
        return "aku"

    @dbus_property(access=PropertyAccess.READ)
    def Title(self) -> "s":
        return "Aku"

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> "s":
        return "Active"

    @dbus_property(access=PropertyAccess.READ)
    def WindowId(self) -> "i":
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def IconName(self) -> "s":
        return ICON_RECORDING if self.tray.recording else ICON_IDLE

    @dbus_property(access=PropertyAccess.READ)
    def IconThemePath(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def OverlayIconName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def AttentionIconName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def ToolTip(self) -> "(sa(iiay)ss)":
        return ["", [], "Aku", "Recording" if self.tray.recording else "Not recording"]

    @dbus_property(access=PropertyAccess.READ)
    def ItemIsMenu(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Menu(self) -> "o":
        return MENU_PATH

    @method()
    def Activate(self, x: "i", y: "i"):
        self.tray.clicked(OPEN)

    @method()
    def SecondaryActivate(self, x: "i", y: "i"):
        self.tray.clicked(TOGGLE)

    @method()
    def ContextMenu(self, x: "i", y: "i"):
        pass

    @method()
    def Scroll(self, delta: "i", orientation: "s"):
        pass

    @signal()
    def NewIcon(self):
        pass

    @signal()
    def NewToolTip(self):
        pass

    @signal()
    def NewStatus(self, status: "s") -> "s":
        return status


class Tray:
    def __init__(self, manager, url: str):
        self.manager = manager
        self.url = url
        self.recording = manager.active is not None
        self.loop = asyncio.get_running_loop()
        self.item = Item(self)
        self.menu = Menu(self)

    def clicked(self, item: int) -> None:
        if item == TOGGLE:
            self.loop.run_in_executor(None, self._toggle)
        elif item == OPEN:
            subprocess.Popen(["xdg-open", self.url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif item == QUIT:
            os.kill(os.getpid(), os_signal.SIGINT)
            # safety net: if a clean shutdown stalls, leave anyway (audio on disk stays recoverable)
            timer = threading.Timer(10, lambda: os._exit(0))
            timer.daemon = True
            timer.start()

    def _toggle(self) -> None:
        self.manager.toggle_safely()

    def refresh(self, recording: bool) -> None:
        if recording == self.recording:
            return
        self.recording = recording
        self.item.NewIcon()
        self.item.NewToolTip()
        self.menu.revision += 1
        self.menu.LayoutUpdated(self.menu.revision, 0)


async def run_tray(manager, url: str) -> None:
    try:
        bus = await MessageBus().connect()
        tray = Tray(manager, url)
        bus.export(ITEM_PATH, tray.item)
        bus.export(MENU_PATH, tray.menu)
        name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        await bus.request_name(name)
        intro = await bus.introspect("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher")
        watcher = bus.get_proxy_object("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", intro)
        await watcher.get_interface("org.kde.StatusNotifierWatcher").call_register_status_notifier_item(name)
    except Exception as e:  # noqa: BLE001 - no tray host: carry on without an icon
        log.warning("tray icon not available (%s); hotkey and browser UI still work", e)
        return

    def on_event(event: dict) -> None:
        if event.get("type") == "status":
            tray.loop.call_soon_threadsafe(tray.refresh, bool(event.get("recording")))

    unsubscribe = manager.bus.subscribe(on_event)
    try:
        await asyncio.Event().wait()
    finally:
        unsubscribe()
        bus.disconnect()
