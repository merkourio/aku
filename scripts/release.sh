#!/usr/bin/env bash
# Publish a release that scripts/get-aku.sh can install: the Aku package with the browser UI
# built in, and a self-contained whisper-server. Run from a clean, committed checkout:
#
#   bash scripts/release.sh 0.2.0 ["What changed, in a sentence or two"]
#
# Bumps the version if needed (and commits that), runs the tests, builds, tags vX.Y.Z,
# pushes, and creates the GitHub release.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${1:?usage: bash scripts/release.sh 0.2.0 [notes]}"
TAG="v$VERSION"
if [ -n "${2:-}" ]; then NOTES=(--notes "$2"); else NOTES=(--generate-notes); fi

[ -z "$(git status --porcelain)" ] || { echo "Commit or stash your changes first."; exit 1; }
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && { echo "$TAG already exists."; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "gh is not logged in. Run: gh auth login"; exit 1; }

CURRENT=$(sed -n 's/^version = "\(.*\)"/\1/p' backend/pyproject.toml)
if [ "$CURRENT" != "$VERSION" ]; then
  sed -i "s/^version = \".*\"/version = \"$VERSION\"/" backend/pyproject.toml
  sed -i "s/^__version__ = \".*\"/__version__ = \"$VERSION\"/" backend/aku/__init__.py
  git commit -qam "Release $VERSION"
fi

echo "== Tests"
(cd backend && uv run pytest -q)
(cd frontend && npm install --no-fund --no-audit && npm test && npm run build)

echo "== whisper.cpp, portable build"
bash scripts/build-whisper.sh --portable
WHISPER="${XDG_DATA_HOME:-$HOME/.local/share}/aku/deps/whisper.cpp"
SERVER="$WHISPER/build-portable/bin/whisper-server"
if ldd "$SERVER" | grep -E 'not found|libwhisper|libggml'; then
  echo "whisper-server is not self-contained (see above)."; exit 1
fi

DIST=$(mktemp -d)
trap 'rm -rf "$DIST"' EXIT
mkdir "$DIST/whisper"
cp "$SERVER" "$DIST/whisper/"
strip "$DIST/whisper/whisper-server"
echo "whisper.cpp $(git -C "$WHISPER" describe --tags --always) (Vulkan)" > "$DIST/whisper/whisper.version"
tar -czf "$DIST/whisper-server-linux-x86_64.tar.gz" -C "$DIST/whisper" whisper-server whisper.version

echo "== Aku package"
(cd backend && uv build --wheel -q -o "$DIST")
WHEEL=$(ls "$DIST"/aku_transcribe-"$VERSION"-*.whl)
python3 -m zipfile -l "$WHEEL" | grep -q "aku/static/index.html" || { echo "The browser UI is missing from the package."; exit 1; }
cp scripts/get-aku.sh scripts/build-whisper.sh "$DIST/"

echo "== Publishing $TAG"
git tag -a "$TAG" -m "Aku $VERSION"
git push -q origin HEAD "$TAG"
gh release create "$TAG" --title "Aku $VERSION" "${NOTES[@]}" \
  "$WHEEL" "$DIST/whisper-server-linux-x86_64.tar.gz" "$DIST/get-aku.sh" "$DIST/build-whisper.sh"
echo
echo "Released $TAG. Install or update on any computer with:"
echo "  gh release download -R merkourio/aku -p get-aku.sh -O - | bash"
