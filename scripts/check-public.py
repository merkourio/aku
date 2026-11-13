#!/usr/bin/env python3
"""Offline release checks; never touches audio hardware, user settings or the network."""
from pathlib import Path
import ast
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    project = tomllib.loads((ROOT / 'backend/pyproject.toml').read_text())['project']
    assert project['license'] == 'AGPL-3.0-or-later'
    assert (ROOT / 'LICENSE').read_bytes() == (ROOT / 'backend/LICENSE').read_bytes()
    assert (ROOT / 'NOTICE').read_bytes() == (ROOT / 'backend/NOTICE').read_bytes()
    assert 'GNU AFFERO GENERAL PUBLIC LICENSE' in (ROOT / 'LICENSE').read_text()
    assert (ROOT / 'backend/licenses/frontend.txt').stat().st_size > 1000
    module = ast.parse((ROOT / 'backend/aku/__init__.py').read_text())
    version = next(ast.literal_eval(n.value) for n in module.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets))
    assert project['version'] == version
    assert re.fullmatch(r'\d+\.\d+\.\d+', version)
    for directory in ('backend/tests', 'frontend/src', 'scripts'):
        for path in (ROOT / directory).rglob('*'):
            if not path.is_file() or path.suffix not in ('.py', '.ts', '.vue', '.sh', '.mjs'):
                continue
            text = path.read_text()
            assert not re.search(r'/home/[A-Za-z0-9_-]+/', text), path
    for name in ('SECURITY.md', 'CONTRIBUTING.md', 'docs/privacy.md', 'docs/releasing.md'):
        assert (ROOT / name).is_file(), name
    print(f'Public-release metadata and privacy checks passed (Aku {version}).')


if __name__ == '__main__':
    main()
