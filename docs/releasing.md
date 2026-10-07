# Notes for developers: template versions and releases

Server owners override built-in templates, stylesheets and scripts through `custom/` (see [customizing.md](customizing.md)).
To warn them when an upgrade changes a file they copied, every such file carries a version line and the repository keeps
a registry of versions (TD-25).

## Versions count from the first release

Every built-in file started at v1 in the first public release (0.1.0): before it, nobody could have an override based on
an older version, so the numbers stayed at v1 (the switch is `FIRST_RELEASE_DONE` in
`src/il2ks/serving/templateversions.py`, now True). `il2ks custom copy` also records the hash of the original, so an
override is flagged `OUT OF DATE` whenever the built-in file changes after it was copied.

**Merging branches that touched templates:** if `template_versions.json` conflicts, take either side and re-run
`uv run il2ks dev bump-templates`: it recomputes every hash from the merged files. If both branches raised the same file's
version, keep the higher number.

## When you change a built-in template, CSS or JS file

1. Edit the file.
2. Run `uv run il2ks dev bump-templates`. It adds the version line to new files (`v1`), raises the version of changed
   files, adds new files to `src/il2ks/web/template_versions.json` and removes deleted ones. Files you did not change
   are left alone. (`--check` changes nothing and exits 1 when something is out of date.)
3. Commit the file together with `template_versions.json`.

A test (`tests/unit/test_template_versions.py`) fails when you forget: a file without a version line, a file missing from
the registry, or a file whose content changed while its version did not. Images (`.svg`, `.png`, fonts) and vendored
libraries (`static/**/vendor/`) have no versions.

Every content change bumps, even a comment or whitespace: the registry compares content, not intent. Make all your
edits to a file before running the command (several edits between two releases still end up as one visible change, but
each run raises the number once more, so don't run it after every small edit).

## Release notes

Template version bumps belong in the release notes: server owners with overrides need to look at exactly those files.
List them with:

```
uv run il2ks dev template-changes v0.2.0      # the previous release tag (or any commit)
```

It compares `template_versions.json` at that tag with the current one and prints one line per file: `v2 -> v3`, `new`,
`removed`. Paste the result under a heading such as "Templates changed (check your custom/ overrides)". Without the
command, `git diff v0.2.0 -- src/il2ks/web/template_versions.json` shows the same thing.

Also mention renamed or removed `{% block %}`s and changed template variables: they break overrides even more directly.

## Making a release

1. Raise `__version__` in `src/il2ks/__init__.py` (the only place) and add a section for it at the top of
   `CHANGELOG.md`, with the template changes from above.
2. Migrations ship as they are: never edit or squash a released one (`il2ks dev check-migrations` guards this).
3. **Audit clean**: the `Dependency audit` workflow (`pip-audit` over `uv.lock`, also run weekly) is green on `main`. A
   flagged package is updated (Dependabot opens the pull request), or, for a false positive, ignored in
   `.github/workflows/audit.yml` with a dated reason. Merge the open Dependabot pull requests you trust first.
4. Commit, wait for CI to pass, then `git tag v<version>` and `git push origin v<version>`. The tag starts
   `.github/workflows/release.yml` (build, smoke test on Linux and Windows, publish to PyPI with trusted publishing) and
   `.github/workflows/windows-installer.yml` (attaches the Windows installer to the GitHub release).
5. Put the changelog section into the GitHub release's notes.
