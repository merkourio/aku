#!/usr/bin/env bash
# Build the pinned whisper.cpp revision, without changing the installed service.
# Requires git, CMake, C++, Vulkan headers/loader and glslc. No sudo/downloaded RPMs.
# AKU_VULKAN_ROOT optionally selects a privately installed Vulkan SDK/sysroot.
# --portable builds a statically linked whisper/ggml server for x86-64 AVX2 CPUs.
set -euo pipefail
umask 077
WHISPER_COMMIT=592feef04a1802b18cbeffd0fd0eb5d02570c2ec
PORTABLE=0
case "${1:-}" in --portable) PORTABLE=1 ;; '') ;; *) echo 'usage: build-whisper.sh [--portable]' >&2; exit 1 ;; esac
DEPS="${AKU_BUILD_ROOT:-${AKU_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/aku}/deps}"
SRC="$DEPS/whisper.cpp"
mkdir -p "$DEPS"
for tool in git cmake g++; do
  command -v "$tool" >/dev/null || { echo "Missing $tool; install build prerequisites." >&2; exit 1; }
done
if [ ! -d "$SRC/.git" ]; then
  # A fixed commit, not a moving branch or whatever happens to be installed locally.
  git init -q "$SRC"
  git -C "$SRC" remote add origin https://github.com/ggml-org/whisper.cpp.git
  git -C "$SRC" fetch -q --depth 1 origin "$WHISPER_COMMIT"
  git -C "$SRC" checkout -q --detach FETCH_HEAD
fi
[ "$(git -C "$SRC" rev-parse HEAD)" = "$WHISPER_COMMIT" ] || { echo "Wrong whisper.cpp revision in $SRC; use a fresh AKU_BUILD_ROOT." >&2; exit 1; }
[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ] || { echo 'Refusing a modified whisper.cpp source tree.' >&2; exit 1; }
BUILD="$SRC/build"
CMAKE_ARGS=(-DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=ON -DWHISPER_BUILD_TESTS=OFF -DWHISPER_COMMON_FFMPEG=OFF)
if [ "$PORTABLE" = 1 ]; then
  BUILD="$SRC/build-portable"
  CMAKE_ARGS+=(-DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_BMI2=ON)
fi
# __FILE__/assert strings remain after strip; remap them at compilation, too.
MAP="-ffile-prefix-map=$SRC=whisper.cpp -fdebug-prefix-map=$SRC=whisper.cpp -ffile-prefix-map=$DEPS=aku-build -ffile-prefix-map=$HOME=/build"
CMAKE_ARGS+=("-DCMAKE_C_FLAGS=$MAP" "-DCMAKE_CXX_FLAGS=$MAP")
if [ -n "${AKU_VULKAN_ROOT:-}" ]; then
  S="$AKU_VULKAN_ROOT"
  CMAKE_ARGS+=("-DCMAKE_PREFIX_PATH=$S" "-DVulkan_INCLUDE_DIR=$S/include" "-DVulkan_GLSLC_EXECUTABLE=$S/bin/glslc")
  # The loader is a system runtime dependency, not part of the private SDK.
  [ ! -e /usr/lib64/libvulkan.so.1 ] || CMAKE_ARGS+=(-DVulkan_LIBRARY=/usr/lib64/libvulkan.so.1)
fi
cmake -S "$SRC" -B "$BUILD" "${CMAKE_ARGS[@]}"
cmake --build "$BUILD" -j"${AKU_BUILD_JOBS:-4}" --target whisper-server
[ "$PORTABLE" = 1 ] || cmake --build "$BUILD" -j"${AKU_BUILD_JOBS:-4}" --target whisper-cli
printf '\nBuilt %s/bin/whisper-server\n' "$BUILD"
