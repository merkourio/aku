#!/usr/bin/env python3
"""Package an already built pinned whisper-server with sanitized metadata/notices."""
from pathlib import Path
import argparse
import hashlib
import io
import re
import platform
import subprocess
import tarfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    src = args.source.resolve()
    output = args.output.resolve()
    binary = src / 'build-portable/bin/whisper-server'
    content = binary.read_bytes()
    if re.search(rb'/(?:home|Users)/[A-Za-z0-9_-]+/', content):
        raise SystemExit('Refusing binary containing a personal build path.')
    revision = subprocess.check_output(['git', '-C', str(src), 'rev-parse', 'HEAD'], text=True).strip()
    top = (src / 'LICENSE').read_text()
    permission = top[top.index('Permission is hereby granted'):]
    mit = lambda copyright: f'MIT License\n\n{copyright}\n\n{permission}'
    mini = (src / 'examples/miniaudio.h').read_text()
    stb = (src / 'examples/stb_vorbis.c').read_text()
    files = {
        'whisper-server': content,
        'whisper.version': f'whisper.cpp {revision} (Vulkan, AVX2)\n'.encode(),
        'licenses/whisper.cpp-MIT.txt': top.encode(),
        'licenses/cpp-httplib-MIT.txt': mit('Copyright (c) 2025 Yuji Hirose. All rights reserved.').encode(),
        'licenses/nlohmann-json-MIT.txt': mit('Copyright (c) 2013-2022 Niels Lohmann <https://nlohmann.me>').encode(),
        'licenses/miniaudio.txt': mini[mini.rindex('This software is available as a choice'):].encode(),
        'licenses/stb_vorbis.txt': stb[stb.rindex('This software is available under 2 licenses'):].encode(),
    }
    root = Path(__file__).resolve().parents[1]
    build_script = (root / 'scripts/build-whisper.sh').read_bytes()
    distribution = platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
    # Fixed flags/source and license provenance are shipped with the binary.
    files['BUILD.txt'] = (f'''whisper.cpp source: https://github.com/ggml-org/whisper.cpp/tree/{revision}
Source commit: {revision}
Build script: https://github.com/merkourio/aku (matching release tag), scripts/build-whisper.sh --portable
Build script SHA256: {hashlib.sha256(build_script).hexdigest()}
Platform: Linux x86-64, AVX2, Vulkan; built on {distribution}.
whisper/ggml are statically linked. glibc, libstdc++, Vulkan loader and GPU drivers are system dependencies.
Build flags/remapping: see the included build-whisper.sh release asset.
Use an isolated AKU_BUILD_ROOT and installed Vulkan development tools (or AKU_VULKAN_ROOT).
No model weights are included. Upstream licenses are in licenses/.
''').encode()
    epoch = int(subprocess.check_output(['git', '-C', str(src), 'show', '-s', '--format=%ct', 'HEAD']))
    output.parent.mkdir(parents=True, exist_ok=True)
    # gzip mtime is fixed as well as tar entry metadata.
    import gzip
    with output.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=epoch) as zipped:
        with tarfile.open(fileobj=zipped, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755 if name == 'whisper-server' else 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = 'root'
                info.mtime = epoch
                archive.addfile(info, io.BytesIO(data))
    print(output)


if __name__ == '__main__':
    main()
