#!/usr/bin/env bash
# Prepare a release locally. Publishing needs the explicit --publish option.
# Usage: release.sh 0.2.0 [notes] [--publish]
# Requires a clean, already versioned checkout. Never auto-bumps/commits versions.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
VERSION="${1:?usage: release.sh X.Y.Z [notes] [--publish]}"
shift
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Invalid stable version.'; exit 1; }
PUBLISH=0
NOTES="Aku $VERSION. See CHANGELOG.md."
for arg in "$@"; do
  if [ "$arg" = --publish ]; then PUBLISH=1; else NOTES="$arg"; fi
done
[ -z "$(git status --porcelain)" ] || { echo 'Commit changes before preparing a release.'; exit 1; }
CURRENT=$(python3 -c 'import tomllib; print(tomllib.load(open("backend/pyproject.toml", "rb"))["project"]["version"])')
[ "$CURRENT" = "$VERSION" ] || { echo 'Update/check project versions before building.'; exit 1; }
python3 scripts/check-public.py
python3 scripts/test-installer.py
(cd backend && uv run --frozen pytest -q)
(cd frontend && npm ci --no-fund --no-audit && npm test && npm run build)
python3 scripts/frontend-notices.py --check
# Separate output, not an installed user dependency tree.
export AKU_BUILD_ROOT="${AKU_BUILD_ROOT:-$PWD/build/whisper}"
bash scripts/build-whisper.sh --portable
SERVER="$AKU_BUILD_ROOT/whisper.cpp/build-portable/bin/whisper-server"
if ldd "$SERVER" | grep -E 'not found|libwhisper|libggml'; then
  echo 'The server has missing libraries or non-static whisper/ggml dependencies.'; exit 1
fi
DIST="$PWD/dist/$VERSION"
[ ! -e "$DIST" ] || { echo "$DIST exists; choose a clean output folder before rebuilding."; exit 1; }
mkdir -p "$DIST"
cp "$SERVER" "$SERVER.unstripped"
strip "$SERVER"
python3 scripts/package-whisper.py "$AKU_BUILD_ROOT/whisper.cpp" "$DIST/whisper-server-linux-x86_64.tar.gz"
(cd backend && uv build --wheel --sdist --out-dir "$DIST")
git archive --format=tar --prefix="aku-$VERSION/" HEAD | gzip -n > "$DIST/aku-$VERSION-source.tar.gz"
cp scripts/get-aku.sh scripts/build-whisper.sh "$DIST/"
python3 - "$DIST" <<'PYCODE'
from pathlib import Path
import hashlib, sys
folder = Path(sys.argv[1])
entries = sorted(p for p in folder.iterdir() if p.is_file() and p.name != 'SHA256SUMS')
(folder / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in entries))
PYCODE
[ -z "$(git status --porcelain)" ] || { echo 'Build changed tracked files; review before publishing.'; exit 1; }
echo "Prepared $DIST. Inspect archives, checksums, licenses and scan for secrets before publishing."
if [ "$PUBLISH" = 1 ]; then
  git rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null && { echo 'Tag already exists; refusing to overwrite it.'; exit 1; }
  git tag -a "v$VERSION" -m "Aku $VERSION"
  git push origin HEAD "v$VERSION"
  gh release create "v$VERSION" "$DIST"/* --title "Aku $VERSION" --notes "$NOTES"
fi
