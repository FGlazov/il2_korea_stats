# Notes for developers: template versions and releases

Server owners override built-in templates, stylesheets and scripts through `custom/` (see [customizing.md](customizing.md)).
To warn them when an upgrade changes a file they copied, every such file carries a version line and the repository keeps
a registry of versions (TD-25).

## Before the first public release: everything stays at v1

Nobody can have an override based on an older version before the first release, so until then **every built-in file stays
at v1**. `uv run il2ks dev bump-templates` only refreshes the content hash of changed files in
`src/il2ks/web/template_versions.json` (action `rehash`); the version lines never change. The switch is the constant
`FIRST_RELEASE_DONE` in `src/il2ks/serving/templateversions.py` (False until the first release). Overrides still get
flagged when the built-in file changes after they were copied: `il2ks custom copy` records the hash of the original, and
a different hash makes the override `OUT OF DATE` even when both say v1.

**Merging branches that touched templates:** version lines are all `v1`, so they no longer conflict. If
`template_versions.json` conflicts, take either side and re-run `uv run il2ks dev bump-templates`: it recomputes every
hash from the merged files. (Same for a template that two branches changed: resolve the file, then re-run.)

**First-release checklist (versions start counting from the first release):**

1. In the release commit, set `FIRST_RELEASE_DONE = True` in `src/il2ks/serving/templateversions.py`.
2. Run `uv run il2ks dev bump-templates --check`: it must say everything is in order (all files stay at v1, which is the
   first release's baseline). Tag the release.
3. From then on the rules below apply: every content change raises the version.

## When you change a built-in template, CSS or JS file (after the first release)

1. Edit the file.
2. Run `uv run il2ks dev bump-templates` (before the first release it only refreshes the hash, see above). It adds the version line to new files (`v1`), raises the version of changed
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
