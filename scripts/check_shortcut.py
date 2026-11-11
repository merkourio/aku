"""Check the global-shortcuts portal as far as possible without a person at the keyboard.

Opens a portal session (no dialog). With --bind it also asks the desktop to bind the
shortcut: the desktop then shows its dialog, and pressing the key afterwards prints a line.

Run:  cd backend && uv run python ../scripts/check_shortcut.py [--bind]
"""

from __future__ import annotations

import asyncio
import sys

from dbus_fast import Variant

from aku.shortcuts import GlobalShortcut


async def main() -> int:
    hits = []
    g = GlobalShortcut(lambda: (hits.append(1), print("shortcut pressed", flush=True)))
    if "--bind" in sys.argv:
        g.enable()
        for _ in range(1200):
            await asyncio.sleep(0.5)
            if g.status["state"] not in ("binding",):
                break
        print("status:", g.status)
        if g.status["state"] == "bound":
            print("Press the shortcut within 30 s…")
            await asyncio.sleep(30)
        await g.disable()
        return 0 if g.status["state"] == "off" else 1
    try:
        await g._connect()
        created = await g._request("CreateSession", "a{sv}", lambda token: [
            {"handle_token": Variant("s", token), "session_handle_token": Variant("s", "akucheck")}], timeout=15)
        g._session = created["session_handle"].value
        print("OK   portal reachable, app registered, session opened:", g._session)
        listed = await g._request("ListShortcuts", "oa{sv}", lambda token: [g._session, {"handle_token": Variant("s", token)}], timeout=15)
        print("OK   shortcuts bound in this session so far:", len(listed["shortcuts"].value))
        ok = True
    except Exception as e:  # noqa: BLE001
        print("FAIL", type(e).__name__, e)
        ok = False
    await g._close()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
