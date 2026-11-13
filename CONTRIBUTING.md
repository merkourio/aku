# Contributing to Aku

Aku is a hobby project built with AI assistance. Issues and pull requests are
welcome, but there is no promised response time or ongoing support commitment.
Contributions are provided under AGPL-3.0-or-later, like Aku itself.

## Privacy first

Use clearly fictional names and synthetic or appropriately licensed test data.
Never attach real calls, transcripts, voiceprints, credentials, raw logs or
screenshots containing personal information. Report vulnerabilities privately
as described in SECURITY.md.

## Offline checks

Python 3.12 or 3.13, uv, Node.js 22.12+ and npm are needed.

```sh
cd backend
uv sync --frozen --group dev
uv run --frozen pytest -q
cd ../frontend
npm ci --no-fund --no-audit
npm test
npm run build
cd ..
python3 scripts/frontend-notices.py --check
python3 scripts/check-public.py
python3 scripts/test-installer.py
```

Unit tests use fake audio and isolated sessions/settings. Integration scripts
under scripts/check_* and frontend/e2e are separate: some restart services,
create PipeWire devices or require models and downloads. Inspect each before
running; never run them against someone else's recording session.

Run Gitleaks against all branches/tags before release; .gitleaks.toml narrowly
allows only the public RFC 6455 sample nonce. Do not add broad secret exclusions.
See docs/releasing.md for release preparation.
