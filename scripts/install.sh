#!/usr/bin/env bash
# Install Aku for your user: the `aku` command, a background service that starts
# when you log in (tray icon + browser UI), and an "Aku" entry in the app launcher.
# No sudo. Run again after updating the code.
#
#   bash scripts/install.sh            install or update (refuses while Aku is recording)
#   bash scripts/install.sh --force    update even if that interrupts a recording
#   bash scripts/install.sh --remove   stop and remove the service, launcher entry and command
set -euo pipefail
cd "$(dirname "$0")/.."

UNIT="$HOME/.config/systemd/user/aku.service"
DESKTOP="$HOME/.local/share/applications/aku.desktop"
ICON="$HOME/.local/share/icons/hicolor/scalable/apps/aku.svg"

if [ "${1:-}" = "--remove" ]; then
  systemctl --user disable --now aku.service 2>/dev/null || true
  rm -f "$UNIT" "$DESKTOP" "$ICON"
  systemctl --user daemon-reload
  uv tool uninstall aku-transcribe 2>/dev/null || true
  echo "Removed. Your recordings and settings are still in ~/.local/share/aku and ~/.config/aku."
  exit 0
fi

# Updating restarts the service. Refuse while it is recording or still working on a transcript.
if [ "${1:-}" != "--force" ] && [ -x "$HOME/.local/bin/aku" ]; then
  BUSY=$("$HOME/.local/bin/aku" status --json 2>/dev/null | python3 -c '
import json, sys
try:
    s = json.load(sys.stdin)
except ValueError:
    sys.exit(0)  # not running: nothing to interrupt
if s.get("recording"):
    print("is recording")
elif s.get("jobs"):
    print("is still finishing a transcript")
' || true)
  if [ -n "$BUSY" ]; then
    echo "Aku $BUSY. Updating now would interrupt it."
    echo "Wait until it is done, or run:  bash scripts/install.sh --force   (a recording stays recoverable)"
    exit 1
  fi
fi

command -v uv >/dev/null || { echo "uv is missing. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
command -v npm >/dev/null || { echo "npm is missing. Install it: sudo dnf install nodejs"; exit 1; }

echo "== Building the browser UI"
(cd frontend && npm install --no-fund --no-audit && npm run build)

if [ ! -x "${XDG_DATA_HOME:-$HOME/.local/share}/aku/deps/whisper.cpp/build/bin/whisper-server" ]; then
  echo "== Building whisper.cpp with Vulkan"
  bash scripts/build-whisper.sh
fi

echo "== Installing the aku command"
uv tool install --force --python 3.12 ./backend

echo "== Service, launcher entry"
mkdir -p "$(dirname "$UNIT")" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
cp frontend/public/favicon.svg "$ICON"
cat > "$UNIT" <<EOF
[Unit]
Description=Aku call transcriber
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart=$HOME/.local/bin/aku serve
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
EOF
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Aku
Comment=Record and transcribe calls locally
Exec=$HOME/.local/bin/aku open
Icon=aku
Categories=AudioVideo;Audio;Utility;
Terminal=false
EOF
systemctl --user daemon-reload
systemctl --user enable aku.service
systemctl --user restart aku.service
sleep 2
"$HOME/.local/bin/aku" doctor || true
echo
echo "Done. Open Aku from the app launcher, or run: aku open"
echo "Hotkey: bind the command  $HOME/.local/bin/aku toggle  in Settings > Keyboard > Custom Shortcuts,"
echo "or run: bash scripts/set-hotkey-gnome.sh '<Super>F9'"
