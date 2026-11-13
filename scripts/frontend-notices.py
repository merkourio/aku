#!/usr/bin/env python3
"""Collect installed lockfile production-dependency notices (including build helpers).

This deliberately over-includes notices rather than guessing which fragments the
bundler retained. Requires npm ci first; use --check in CI.
"""
from pathlib import Path
import argparse
import json

ROOT = Path(__file__).resolve().parents[1]


def render() -> str:
    lock = json.loads((ROOT / 'frontend/package-lock.json').read_text())
    out = ['Frontend third-party notices\n\nGenerated from frontend/package-lock.json.\n'
           'Includes production dependencies and their build helpers; not all are bundled.\n']
    for name, package in sorted(lock['packages'].items()):
        if not name or package.get('dev'):
            continue
        folder = ROOT / 'frontend' / name
        installed = json.loads((folder / 'package.json').read_text())
        if installed['version'] != package['version']:
            raise ValueError(f'{name}: run npm ci; installed version differs from lockfile')
        notices = sorted(p for p in folder.iterdir() if p.is_file() and
                         p.name.lower().startswith(('license', 'licence', 'copying', 'notice')))
        if not notices:
            raise ValueError(f'{name}: missing license text; review upstream before release')
        out.append(f"\n{'=' * 72}\n{name.removeprefix('node_modules/')} {package['version']}\n")
        for notice in notices:
            out.append(f'\n--- {notice.name} ---\n{notice.read_text().strip()}\n')
    return ''.join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    output = ROOT / 'backend/licenses/frontend.txt'
    content = render()
    if args.check:
        if not output.exists() or output.read_text() != content:
            raise SystemExit('Frontend notices are stale: run python3 scripts/frontend-notices.py')
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content)


if __name__ == '__main__':
    main()
