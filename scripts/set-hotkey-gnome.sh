#!/usr/bin/env bash
# Bind a key to "aku toggle" on GNOME (start recording with the last settings, or stop).
#   bash scripts/set-hotkey-gnome.sh '<Super>F9'
# On KDE: System Settings > Shortcuts > Add New > Command, and enter the same command.
set -euo pipefail
KEY="${1:?Give a key, for example '<Super>F9'}"
SCHEMA=org.gnome.settings-daemon.plugins.media-keys
PATH_="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/aku/"
CURRENT=$(gsettings get $SCHEMA custom-keybindings)
if [[ "$CURRENT" != *"$PATH_"* ]]; then
  if [[ "$CURRENT" == "@as []" || "$CURRENT" == "[]" ]]; then NEW="['$PATH_']"; else NEW="${CURRENT%]}, '$PATH_']"; fi
  gsettings set $SCHEMA custom-keybindings "$NEW"
fi
gsettings set $SCHEMA.custom-keybinding:$PATH_ name "Aku: start/stop recording"
gsettings set $SCHEMA.custom-keybinding:$PATH_ command "$HOME/.local/bin/aku toggle"
gsettings set $SCHEMA.custom-keybinding:$PATH_ binding "$KEY"
echo "Bound $KEY to: aku toggle"
