#!/usr/bin/env bash
# Check: "Quit Aku" in the tray really exits, even with the UI open in a browser tab,
# and `aku open` brings the installed service back afterwards.
# Runs against the installed service (systemd unit aku.service). It opens a browser tab at the end.
set -u
PORT=$(python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/.config/aku/settings.json'))).get('port', 4587))" 2>/dev/null || echo 4587)
FAIL=0
ok() { echo "OK   $1"; }
bad() { echo "FAIL $1"; FAIL=1; }
up() { curl -s -m 1 -o /dev/null "http://127.0.0.1:$PORT/status"; }

systemctl --user restart aku.service
for _ in $(seq 40); do up && break; sleep 0.25; done
up && ok "service running" || bad "service did not start"
PID=$(systemctl --user show -p MainPID --value aku.service)

# hold a WebSocket open, like a browser tab does
curl -s -N -m 60 -o /dev/null -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" "http://127.0.0.1:$PORT/live" &
WS=$!
sleep 1

gdbus call --session --dest "org.kde.StatusNotifierItem-$PID-1" --object-path /MenuBar \
  --method com.canonical.dbusmenu.Event 4 clicked "<0>" 0 >/dev/null   # item 4 = Quit Aku
for _ in $(seq 24); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
kill -0 "$PID" 2>/dev/null && bad "still running 12 s after Quit" || ok "Quit exits with a tab connected"
kill $WS 2>/dev/null

"$HOME/.local/bin/aku" open && sleep 1
up && ok "aku open started it again" || bad "aku open did not bring it back"
[ $FAIL = 0 ] && echo "RESULT: PASS" || echo "RESULT: FAIL"
exit $FAIL
