#!/usr/bin/env bash
# Build whisper.cpp with Vulkan (GPU) into ~/.local/share/aku/deps/whisper.cpp.
#
# Needs: git, cmake, g++, plus the Vulkan build files. If those are installed
# system-wide (sudo dnf install vulkan-headers vulkan-loader-devel glslc spirv-headers-devel)
# they are used. If not, this script downloads the Fedora packages and unpacks
# them into a private folder: no sudo, nothing installed system-wide.
#
#   bash scripts/build-whisper.sh              for this computer (what the installers use as a fallback)
#   bash scripts/build-whisper.sh --portable   for a release: one self-contained file that runs on other
#                                              computers too (no -march=native, libraries linked in)
set -euo pipefail
PORTABLE=0
[ "${1:-}" = "--portable" ] && PORTABLE=1

DEPS="${AKU_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/aku}/deps"
SRC="$DEPS/whisper.cpp"
SYSROOT="$DEPS/sysroot"
mkdir -p "$DEPS"

for tool in git cmake g++; do
  command -v "$tool" >/dev/null || { echo "Missing $tool. Install it with: sudo dnf install git cmake gcc-c++"; exit 1; }
done

if [ ! -d "$SRC/.git" ]; then
  git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$SRC"
fi

CMAKE_ARGS=(-DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=1 -DWHISPER_BUILD_TESTS=OFF)
BUILD="$SRC/build"
if [ "$PORTABLE" = 1 ]; then
  BUILD="$SRC/build-portable"
  # x86-64-v3 (AVX2, any CPU from about 2015 on); the GPU does the heavy work anyway
  CMAKE_ARGS+=(-DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_BMI2=ON)
fi

if [ -f /usr/include/vulkan/vulkan.h ] && command -v glslc >/dev/null; then
  echo "Using system Vulkan build files."
else
  echo "Vulkan build files not installed: fetching them into $SYSROOT (no sudo needed)."
  mkdir -p "$DEPS/rpms" "$SYSROOT"
  (cd "$DEPS/rpms" && dnf download --assumeno --arch x86_64 --arch noarch \
      vulkan-headers vulkan-loader-devel glslc spirv-headers-devel </dev/null || true)
  ls "$DEPS"/rpms/*.rpm >/dev/null 2>&1 || { echo "Could not download the packages. Install them with sudo dnf instead (see top of this script)."; exit 1; }
  for rpm in "$DEPS"/rpms/*.rpm; do (cd "$SYSROOT" && rpm2cpio "$rpm" | cpio -idm --quiet); done
  S="$SYSROOT/usr"
  CMAKE_ARGS+=(-DCMAKE_PREFIX_PATH="$S" -DVulkan_INCLUDE_DIR="$S/include" \
               -DVulkan_LIBRARY=/usr/lib64/libvulkan.so.1 -DVulkan_GLSLC_EXECUTABLE="$S/bin/glslc")
fi

cmake -S "$SRC" -B "$BUILD" "${CMAKE_ARGS[@]}"
cmake --build "$BUILD" -j"$(nproc)" --target whisper-server $([ "$PORTABLE" = 1 ] || echo whisper-cli)
echo
echo "Built: $BUILD/bin/whisper-server"
echo "Check the GPU is used:  aku doctor"
