#!/usr/bin/env python3
"""Installer regression tests: no network, packages, services or real user data."""
from pathlib import Path
import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent / 'get-aku.sh'


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aku-installer-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.assets = self.root / 'assets'
        self.assets.mkdir()
        (self.assets / 'aku_transcribe-0.2.0-py3-none-any.whl').write_bytes(b'test wheel, not installed')
        (self.assets / 'build-whisper.sh').write_text('#!/bin/sh\nexit 99\n')
        with tarfile.open(self.assets / 'whisper-server-linux-x86_64.tar.gz', 'w:gz') as archive:
            data = b'#!/bin/sh\nexit 0\n'
            info = tarfile.TarInfo('whisper-server')
            info.size = len(data)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(data))
        self.sums()
        self.tool('pw-record', '#!/bin/sh\nexit 0\n')
        self.tool('sleep', '#!/bin/sh\nexit 0\n')
        self.tool('curl', '''#!/usr/bin/env python3
import os, pathlib, shutil, sys
args = sys.argv[1:]
url = next(x for x in args if x.startswith('https://'))
if '--write-out' in args:
    print('https://github.com/merkourio/aku/releases/tag/v0.2.0', end='')
else:
    destination = pathlib.Path(args[args.index('--output') + 1])
    shutil.copyfile(pathlib.Path(os.environ['TEST_ASSETS']) / url.rsplit('/', 1)[1], destination)
''')
        self.tool('uv', '''#!/bin/sh
printf '%s\n' "$*" >> "$HOME/uv-calls"
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/aku" <<'SH'
#!/bin/sh
printf '%s\n' "$*" >> "$HOME/aku-calls"
exit 0
SH
chmod +x "$HOME/.local/bin/aku"
''')
        self.env = {**os.environ, 'HOME': str(self.home), 'XDG_DATA_HOME': str(self.home / 'data'),
                    'PATH': str(self.bin) + os.pathsep + os.environ['PATH'], 'TEST_ASSETS': str(self.assets)}

    def tool(self, name, content):
        p = self.bin / name
        p.write_text(content)
        p.chmod(0o755)

    def sums(self):
        text = ''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
                       for p in sorted(self.assets.iterdir()) if p.name != 'SHA256SUMS')
        (self.assets / 'SHA256SUMS').write_text(text)

    def run_installer(self, *args):
        return subprocess.run(['bash', str(SCRIPT), *args], env=self.env, text=True,
                              capture_output=True, timeout=30)

    def test_pinned_install_verifies_before_installing(self):
        result = self.run_installer('--version', 'v0.2.0')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('SHA-256 checksums verified', result.stdout)
        self.assertIn('tool install', (self.home / 'uv-calls').read_text())
        self.assertIn('setup', (self.home / 'aku-calls').read_text())

    def test_latest_uses_anonymous_redirect(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Downloading Aku v0.2.0', result.stdout)

    def test_bad_checksum_refuses_before_installing(self):
        (self.assets / 'aku_transcribe-0.2.0-py3-none-any.whl').write_bytes(b'tampered')
        result = self.run_installer('--version', 'v0.2.0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Checksum verification failed', result.stderr)
        self.assertFalse((self.home / 'uv-calls').exists())

    def test_bad_version_is_rejected(self):
        result = self.run_installer('--version', '../../bad')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / 'uv-calls').exists())

    def test_missing_version_is_rejected(self):
        self.assertNotEqual(self.run_installer('--version').returncode, 0)

    def test_busy_recording_is_not_interrupted(self):
        p = self.home / '.local/bin/aku'
        p.parent.mkdir(parents=True)
        p.write_text('#!/bin/sh\necho \'{"recording": true}\'\n')
        p.chmod(0o755)
        result = self.run_installer('--version', 'v0.2.0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('is recording', result.stderr)
        self.assertFalse((self.home / 'uv-calls').exists())


if __name__ == '__main__':
    unittest.main()
