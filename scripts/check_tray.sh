#!/usr/bin/env bash
# M6 check: tray icon registers with the desktop, its menu works, `aku toggle` starts and stops.
# Starts a throwaway service on a spare port. Uses a silent virtual device for the recording.
# Run from the repo:  bash scripts/check_tray.sh
set -u
cd "$(dirname "$0")/../backend"
TMP=$(mktemp -d)
export AKU_SESSIONS_DIR=$TMP/sessions AKU_DB=$TMP/aku.db AKU_CONFIG_DIR=$TMP/config
mkdir -p "$AKU_CONFIG_DIR"
echo '{"port": 4596, "whisper_port": 4595, "notifications": false, "last_sources": {"input": "aku_check_out", "output": "aku_check_out", "scope": "all", "apps": []}}' > "$AKU_CONFIG_DIR/settings.json"
MOD=$(pactl load-module module-null-sink sink_name=aku_check_out sink_properties=device.description=aku_check_out)
FAIL=0
ok() { echo "OK   $1"; }
bad() { echo "FAIL $1"; FAIL=1; }

uv run aku serve > "$TMP/serve.log" 2>&1 &
PID=$!
for _ in $(seq 50); do curl -s -o /dev/null http://127.0.0.1:4596/status && break; sleep 0.2; done
sleep 1

ITEMS=$(gdbus call --session --dest org.kde.StatusNotifierWatcher --object-path /StatusNotifierWatcher \
  --method org.freedesktop.DBus.Properties.Get org.kde.StatusNotifierWatcher RegisteredStatusNotifierItems 2>&1)
NAME=$(busctl --user list --no-legend 2>/dev/null | awk '{print $1}' | grep "^org.kde.StatusNotifierItem-" | while read -r n; do
  [ "$(busctl --user status "$n" 2>/dev/null | awk -F= '/^PID=/{print $2}')" = "$(pgrep -P $PID -n python || echo $PID)" ] && echo "$n"; done | head -1)
[ -z "$NAME" ] && NAME=$(grep -o "org.kde.StatusNotifierItem-[0-9]*-1" <<<"$ITEMS" | tail -1)
if grep -q "StatusNotifierItem" <<<"$ITEMS" && [ -n "$NAME" ]; then ok "tray item registered with the desktop ($NAME)"; else bad "tray item not registered: $ITEMS $(cat "$TMP/serve.log")"; fi

icon() { gdbus call --session --dest "$NAME" --object-path /StatusNotifierItem --method org.freedesktop.DBus.Properties.Get org.kde.StatusNotifierItem IconName; }
menu() { gdbus call --session --dest "$NAME" --object-path /MenuBar --method com.canonical.dbusmenu.GetLayout 0 10 "[]"; }
grep -q "audio-input-microphone" <<<"$(icon)" && ok "idle icon" || bad "idle icon: $(icon)"
grep -q "Start recording" <<<"$(menu)" && grep -q "Open Aku" <<<"$(menu)" && ok "menu has Start recording / Open Aku" || bad "menu: $(menu)"

# start from the tray menu (item 1 = toggle), stop with the hotkey command
gdbus call --session --dest "$NAME" --object-path /MenuBar --method com.canonical.dbusmenu.Event 1 clicked "<0>" 0 >/dev/null
sleep 2
grep -q '"recording":true' <<<"$(curl -s http://127.0.0.1:4596/status)" && ok "tray menu starts a recording" || bad "tray start"
grep -q "media-record" <<<"$(icon)" && grep -q "Stop recording" <<<"$(menu)" && ok "icon and menu switch to the recording state" || bad "recording state: $(icon)"
uv run aku toggle | grep -q "stopped" && ok "aku toggle stops it" || bad "aku toggle stop"
sleep 1
grep -q '"recording":false' <<<"$(curl -s http://127.0.0.1:4596/status)" && grep -q "audio-input-microphone" <<<"$(icon)" && ok "back to idle" || bad "idle after stop"
uv run aku toggle | grep -q "started" && ok "aku toggle starts with the last settings" || bad "aku toggle start"
sleep 1
uv run aku status | grep -q "Recording for" && ok "aku status reports it" || bad "aku status"
uv run aku stop >/dev/null; sleep 3

kill -INT $PID; wait $PID 2>/dev/null
pactl unload-module "$MOD"
rm -rf "$TMP"
[ $FAIL = 0 ] && echo "RESULT: PASS" || echo "RESULT: FAIL"
exit $FAIL
