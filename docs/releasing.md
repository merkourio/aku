# Releasing

1. Update backend/pyproject.toml, backend/aku/__init__.py and CHANGELOG.md together.
   Refresh frontend notices after dependency changes. Commit reviewed changes.
2. Run the offline checks in CONTRIBUTING.md, Gitleaks on all refs, npm audit,
   and pip-audit on the actual installed Python dependency set. Review findings;
   a successful build alone is not security approval.
3. With CMake/C++/Vulkan development files installed, run:

   ```sh
   bash scripts/release.sh X.Y.Z
   ```

   This builds locally only; it neither pushes nor publishes. It uses a pinned
   whisper.cpp revision, remaps build paths, includes third-party notices and
   normalizes archive ownership. AVX2 and system Vulkan/glibc are still needed;
   “portable” does not mean a fully static universal Linux executable.
4. Inspect dist/X.Y.Z: wheel, Python sdist, full source archive, whisper-server
   archive, installers and SHA256SUMS. The full source archive contains the Vue
   sources, lockfiles and build scripts needed to rebuild the frontend.
5. Test a clean wheel installation in a disposable environment (no models,
   microphone, systemd setup or cloud API required for --version/UI smoke checks).
   Validate installer refusal on bad checksums. Do not interrupt real recordings.
6. Only after review, create/push the matching immutable tag and publish the
   inspected assets. The optional --publish flag on release.sh combines build
   and publication; never use it merely to test a build. Do not move public tags
   or replace published assets silently; make a new version instead.
7. Confirm anonymous downloads, checksums and CI after publication.

## Repository configuration

Public repository settings: private vulnerability reporting, Dependabot alerts
and fixes, protected master (passing Backend (3.12), Backend (3.13), Frontend,
Secrets checks; no force pushes/deletion) and immutable v* tags. Restrict write
access. CI workflows run only on public repositories to avoid private-run costs.
Maintainers can bypass rules for controlled release administration.

## Initial private-to-public preparation

The earlier private v0.2.0 tag/assets were replaced before the first public
release to sanitize identifiers and add licensing. Old private history stays
only in an owner-only local recovery backup, never a public backup branch/tag.
Existing private collaborators must re-clone; merging old history reintroduces
identifiers. Review all branches, tags, downloads, issues and Actions logs
before changing visibility. Publishing a newer release does not hide older ones.
