---
name: release
description: Publish an il2ks release (patch, minor or major). Use it whenever the maintainer asks for a release or a version bump. It covers the version, changelog, README status line, design doc note, CI, the tag, the release workflows and the GitHub notes.
---

# Make a release

`docs/releasing.md` is the reference (template versions, the audit, bundled components). This is the order to follow.

1. **Check**: `uv run il2ks dev check --fix --full`, then the browser flows for real:
   `IL2KS_TEST_E2E=1 uv run pytest tests/e2e -m e2e -q` (without the variable they are skipped, which looks like a pass).
   Run long jobs in the background and start `keep-awake`.
2. **Version**: raise `__version__` in `src/il2ks/__init__.py`, the only place the code reads it.
3. **README status line**: "**Status: public beta, version X.Y.Z.**" near the top of `README.md` (maintainer 2026-10-31). A test
   fails until it matches. Read the feature list too and fix what the release made untrue.
4. **Changelog**: a `## X.Y.Z (date)` section at the top of `CHANGELOG.md`, in words a server owner understands. Add "Templates
   changed (check your custom/ overrides)" from `uv run il2ks dev template-changes v<previous>`, and say what to run after the
   upgrade (`il2ks rebuild-aggregates`, `il2ks reprocess --all`) when a release needs it.
5. **Design doc**: a revision line in `design_doc/README.md`.
6. **Commit and push**: explicit paths, on `main`. Then watch CI (`watch-ci`): every job green, Postgres and e2e included.
7. **Tag the commit CI passed, by SHA**, not whatever HEAD is by then:
   `git tag v<version> <sha>` and `git push origin v<version>`. Never tag a red or unfinished run.
8. **Watch the tag's runs**: `release.yml` (PyPI) and `windows-installer.yml` (the `.exe` on the GitHub release).
9. **Notes**: put the changelog section into the GitHub release with `gh release edit v<version> --notes-file <file>`. Write that
   file from Python with `encoding="utf-8"`, not from PowerShell, which can add a BOM or mangle the text.
10. **Report**: what shipped, what server owners must do, and anything left open (translations still llm-draft, open OQs).

PyPI can't take the same version twice, so anything wrong after step 7 needs the next patch version.

## What needs no new release

The GitHub page is what matters for documentation (maintainer 2026-10-31): a README or docs fix after the tag just goes to `main`.
There's no need to retag or to wait for its CI first, and the tag can go on the commit CI already checked. The copy of the README
inside the package may then lag, which is fine.
