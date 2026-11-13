#!/usr/bin/env bash
# Install/update the public GitHub release, without a GitHub account.
# Download and inspect this script before running it; see README.md.
# --version vX.Y.Z pins a stable release; --force permits updating a busy Aku.
# --remove keeps recordings/settings. Installation enables a login-started service.
set -euo pipefail
umask 077
REPO=merkourio/aku
TAG=""
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --version) [ $# -ge 2 ] || { echo '--version needs vX.Y.Z' >&2; exit 1; }; TAG="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --remove)
      [ -x "$HOME/.local/bin/aku" ] && "$HOME/.local/bin/aku" setup --remove || true
      command -v uv >/dev/null && uv tool uninstall aku-transcribe 2>/dev/null || true
      rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/aku/deps/bin"
      echo 'Removed Aku. Recordings/settings are kept in ~/.local/share/aku and ~/.config/aku.'
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
say() { printf '\n== %s\n' "$*"; }
fail() { printf '\nAku is not installed: %s\n' "$*" >&2; exit 1; }
[ "$(uname -s)-$(uname -m)" = Linux-x86_64 ] || fail 'Only Linux x86-64 is supported (binary needs AVX2).'
for tool in curl python3 sha256sum tar uv; do
  command -v "$tool" >/dev/null || fail "Missing $tool. Install prerequisites first; uv instructions: https://docs.astral.sh/uv/getting-started/installation/"
done
command -v pw-record >/dev/null || fail 'PipeWire tools are missing (Fedora: sudo dnf install pipewire-utils).'

# Updating restarts Aku. Refuse while recording or finishing a transcript.
if [ "$FORCE" = 0 ] && [ -x "$HOME/.local/bin/aku" ]; then
  BUSY=$("$HOME/.local/bin/aku" status --json 2>/dev/null | python3 -c '
import json, sys
try:
    s = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
print("is recording" if s.get("recording") else "is still finishing a transcript" if s.get("jobs") else "")
' 2>/dev/null || true)
  [ -z "$BUSY" ] || fail "Aku $BUSY. Wait, or use --force (audio stays recoverable)."
fi

fetch() { curl --fail --silent --show-error --location --retry 3 --proto '=https' "$@"; }
if [ -z "$TAG" ]; then
  URL=$(fetch --output /dev/null --write-out '%{url_effective}' "https://github.com/$REPO/releases/latest")
  TAG=${URL##*/}
fi
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'Invalid release version; use vX.Y.Z.'
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
BASE="https://github.com/$REPO/releases/download/$TAG"
WHEEL="aku_transcribe-${TAG#v}-py3-none-any.whl"
FILES=("$WHEEL" whisper-server-linux-x86_64.tar.gz build-whisper.sh)
say "Downloading Aku $TAG"
fetch "$BASE/SHA256SUMS" --output "$TMP/SHA256SUMS"
for file in "${FILES[@]}"; do
  fetch "$BASE/$file" --output "$TMP/$file"
done
# Only verify the files we requested; never interpret arbitrary paths from a manifest.
python3 - "$TMP" "${FILES[@]}" <<'PY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
rows = {}
for line in (root / 'SHA256SUMS').read_text().splitlines():
    digest, name = line.split(maxsplit=1)
    name = name.lstrip('* ')
    if name in rows:
        raise SystemExit('Duplicate checksum entry')
    rows[name] = digest
for name in sys.argv[2:]:
    digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
    if digest != rows.get(name):
        raise SystemExit(f'Checksum verification failed: {name}')
print('SHA-256 checksums verified.')
PY

say 'Installing Aku (Python 3.12 and CPU libraries; first install is about 1 GB)'
uv tool install --force --python 3.12 --torch-backend cpu "$TMP/$WHEEL"
AKU="$HOME/.local/bin/aku"
say 'Installing the local transcription engine'
BIN="${XDG_DATA_HOME:-$HOME/.local/share}/aku/deps/bin"
mkdir -p "$BIN"
tar --extract --gzip --file "$TMP/whisper-server-linux-x86_64.tar.gz" --directory "$BIN" --no-same-owner --no-same-permissions
if ! "$BIN/whisper-server" --help >/dev/null 2>&1; then
  echo 'The binary does not run here; building the pinned whisper.cpp revision instead.'
  rm -f "$BIN/whisper-server"
  bash "$TMP/build-whisper.sh" || fail 'Build failed; install CMake, git, C++ and Vulkan build tools, then retry.'
fi
say 'Enabling the background service at login and adding the launcher entry'
"$AKU" setup
sleep 2
"$AKU" doctor || { echo 'Installation completed, but doctor reported a problem; inspect its output.' >&2; exit 1; }
echo "Done. Open Aku from your launcher or run: aku open"
