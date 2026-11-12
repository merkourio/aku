#!/usr/bin/env bash
# Install or update Aku from its latest GitHub release. No sudo, nothing to compile.
#
# The repository is private, so this uses your GitHub login through the `gh` command:
#   gh auth login                                                    (once per computer)
#   gh release download -R merkourio/aku -p get-aku.sh -O - | bash   (install, and again to update)
#
# Options (after `bash -s --`):  --version v0.2.0   a specific release instead of the latest
#                                --force            update even while Aku is recording
#                                --remove           remove Aku (recordings and settings are kept)
set -euo pipefail

REPO=merkourio/aku
TAG=""
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --version) TAG="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --remove)
      [ -x "$HOME/.local/bin/aku" ] && "$HOME/.local/bin/aku" setup --remove || true
      command -v uv >/dev/null && uv tool uninstall aku-transcribe 2>/dev/null || true
      rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/aku/deps/bin"
      echo "Removed. Your recordings and settings are still in ~/.local/share/aku and ~/.config/aku."
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

say() { printf '\n== %s\n' "$*"; }
fail() { printf '\nAku is not installed: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)-$(uname -m)" = "Linux-x86_64" ] || fail "Aku only runs on 64-bit Intel/AMD Linux."
command -v gh >/dev/null || fail "the gh command is missing. Install it (Fedora: sudo dnf install gh), then run: gh auth login"
gh auth status >/dev/null 2>&1 || fail "gh is not logged in to GitHub. Run: gh auth login"
command -v pw-record >/dev/null || echo "Note: PipeWire's tools are missing (Fedora: sudo dnf install pipewire-utils). Aku needs them to record."

# Updating restarts Aku. Refuse while it is recording or still working on a transcript.
if [ "$FORCE" = 0 ] && [ -x "$HOME/.local/bin/aku" ]; then
  BUSY=$("$HOME/.local/bin/aku" status --json 2>/dev/null | python3 -c '
import json, sys
try:
    s = json.load(sys.stdin)
except ValueError:
    sys.exit(0)  # not running: nothing to interrupt
print("is recording" if s.get("recording") else "is still finishing a transcript" if s.get("jobs") else "")
' 2>/dev/null || true)
  if [ -n "$BUSY" ]; then
    echo "Aku $BUSY. Updating now would interrupt it."
    echo "Wait until it is done, or add --force (a recording stays recoverable)."
    exit 1
  fi
fi

if ! command -v uv >/dev/null; then
  say "Installing uv (Python package manager, into ~/.local/bin)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
say "Downloading Aku ${TAG:-(latest release)}"
gh release download ${TAG:+"$TAG"} -R "$REPO" -D "$TMP" \
  -p 'aku_transcribe-*.whl' -p 'whisper-server-linux-x86_64.tar.gz' -p 'build-whisper.sh'
WHEEL=$(ls "$TMP"/aku_transcribe-*.whl)

say "Installing the aku command (the first time this downloads Python and its libraries, about 1 GB)"
uv tool install --force --python 3.12 --torch-backend cpu "$WHEEL"
AKU="$HOME/.local/bin/aku"

say "Installing the transcription engine (whisper.cpp)"
BIN="${XDG_DATA_HOME:-$HOME/.local/share}/aku/deps/bin"
mkdir -p "$BIN"
tar -xzf "$TMP/whisper-server-linux-x86_64.tar.gz" -C "$BIN"
if "$BIN/whisper-server" --help >/dev/null 2>&1; then
  echo "Ready: $(cat "$BIN/whisper.version" 2>/dev/null || echo whisper-server)"
else
  echo "The ready-made engine does not run on this computer. Building it here instead (a few minutes)."
  rm -f "$BIN/whisper-server"
  bash "$TMP/build-whisper.sh" || fail "whisper.cpp could not be built. Install git, cmake and gcc-c++ and run this again."
fi

say "Background service and launcher entry"
"$AKU" setup
sleep 2
"$AKU" doctor || true
echo
echo "Done: Aku $("$AKU" --version). Open it from the app launcher, or run: aku open"
echo "To update later, run the same command again."
