# Public-release checks — 2026-11-13

Performed against the sanitized source and rebuilt v0.2.0 assets on Fedora 44.

- Backend: 82 tests pass in fresh locked environments on Python 3.12 and 3.13.
- Frontend: 8 Vitest tests pass; vue-tsc and Vite production build pass.
- Installer: 6 isolated regression tests pass, including checksum rejection,
  anonymous latest-version selection, version validation and busy-recording guard.
- Clean wheel install: --version, built UI, settings/sessions API and cross-origin
  refusal pass in a disposable environment. No installed user service changed.
- AGPL source link verified at 1200px and 390px with mocked data and no browser
  errors. The existing narrow-screen navigation has about 8px horizontal overflow;
  this release preparation does not claim a full responsive-UI review.
- npm audit: no known vulnerabilities after removing unused @vue/test-utils and
  its vulnerable development-only dependency chain.
- pip-audit: no known vulnerabilities in 128 recognized installed packages.
  Aku itself and CPU-specific torch/torchaudio builds were not recognized by the
  PyPI audit service; this is not a complete security assessment of those builds.
- Gitleaks 8.30.1: sanitized Git history checked; the only scoped exclusion is
  RFC 6455's public sample WebSocket handshake nonce, never an API credential.
- whisper.cpp pinned at the revision in scripts/build-whisper.sh; compiled with
  source-path remapping. Binary and archive contents checked for personal paths;
  archive ownership normalized and bundled notices included.

No real recording, cloud API, model inference, desktop integration installation,
GPU transcription or multi-distribution compatibility test was run as part of
this publication cleanup. Existing feature tests are not a new hardware claim.
GitHub may retain orphaned objects from the earlier private history; the owner
chose to reuse the repository with that limitation instead of creating a new one.
