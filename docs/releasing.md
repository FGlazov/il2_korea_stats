# Notes for developers: template versions and releases

Server owners override built-in templates, stylesheets and scripts through `custom/` (see [customizing.md](customizing.md)).
To warn them when an upgrade changes a file they copied, every such file carries a version line and the repository keeps
a registry of versions (TD-25).

## Versions: vN.M

Every built-in file carries `vN.M` in its first line (0.1.0 shipped `vN`; a header without `.M` reads as `vN.0` everywhere,
in the tool, in `il2ks custom` and in the admin banner). **N** is the override contract: it goes up when an owner's copy of
the file can break or hide new content. **M** is everything else. An older N makes an override `outdated` (red banner,
installer message box, doctor warning), an older M only `behind` (shown by `il2ks custom list`).

What counts as a contract change (`src/il2ks/serving/templatecontract.py` is the authority; it reads files lexically):

| File | N goes up when ... | Otherwise M |
|---|---|---|
| Template | a `{% block %}`, `{% extends %}` or `{% include %}` target is added, removed or renamed; a context variable (root name, like `player` of `{{ player.name }}`) is added or removed; an il2ks tag or filter is **removed**; an id, class or `data-` attribute that **any built-in stylesheet or script targets** is added or removed in the markup | wording, markup, classes nothing targets, Django's own tags and filters |
| Stylesheet | a class, id, `data-` attribute selector or `--custom-property` is added or removed (a copy of the CSS lacks what newer templates use) | values, colours, spacing |
| Script | an id, class or `data-` attribute it looks up (`getElementById`, `querySelector`, `closest`, `classList`, `dataset`) is added or removed | logic |

`il2ks dev bump-templates` stores a **fingerprint** of every file's contract in `src/il2ks/web/template_versions.json`
(next to the version and the content hash) and compares a changed file with it. We chose the stored fingerprint over
reading the old file from git (HEAD): it works in a shallow clone, in an sdist, on an uncommitted branch, and after a
merge, whereas "the file in HEAD" is wrong as soon as someone commits without bumping. The price: after a change to the
extractor itself, the tool says "contract fingerprint recorded" once; run it and commit.

**Merging branches that touched templates:** if `template_versions.json` conflicts, take the side of the branch you merge
into (main) and re-run `uv run il2ks dev bump-templates`: it compares every merged file with that side's fingerprint,
so the part (N or M) is judged again for the merged result. A file whose header a branch raised with the 0.1.0-style
single number (`v4`) is judged again too; a header you raised by hand with a minor number (`v4.0`) is believed.

## When you change a built-in template, CSS or JS file

1. Edit the file.
2. Run `uv run il2ks dev bump-templates`. It adds the version line to new files (`v1.0`), raises the version of changed
   files (and says in a line for each whether it was **BREAKING, N** and why, or M), adds new files to
   `src/il2ks/web/template_versions.json` and removes deleted ones. Files you did not change are left alone.
   (`--check` changes nothing and exits 1 when something is out of date.)
3. Commit the file together with `template_versions.json`.

`--major` forces N for a change the diff cannot see (a behaviour change that overrides must follow, for instance a
different meaning of a variable): `bump-templates --major il2ks/home.html` (a path inside `templates/` or `static/`; without
a name it applies to every changed file). Use it on the first run for the file; a file already bumped can be raised by
hand: edit the header to the next N, `v5.0`, and run the tool, which believes a hand-raised version.

A test (`tests/unit/test_template_versions.py`) fails when you forget: a file without a version line, a file missing from
the registry, or a file whose content changed while its version did not. Images (`.svg`, `.png`, fonts) and vendored
libraries (`static/**/vendor/`) have no versions.

Every content change bumps (M at least), even a comment or whitespace: the registry compares content, not intent. Make all
your edits to a file before running the command (each run raises M once more, so don't run it after every small edit).

## Release notes

The release notes list **only the N bumps** (and removed files): those are the files whose overrides need a look. M
bumps are noise. Get the list with:

```
uv run il2ks dev template-changes v0.2.0      # the previous release tag (or any commit)
uv run il2ks dev template-changes v0.2.0 --all   # also the M-only bumps and new files
```

It compares `template_versions.json` at that tag with the current one and prints one line per file, like
`templates/il2ks/home.html: v2.0 -> v3.0`, and `removed`. A tag from 0.1.0 has single numbers, read as `N.0`, so the
list is exact for the first upgrade too. Paste the result under a heading such as "Templates changed (check your
custom/ overrides)". Without the command, `git diff v0.2.0 -- src/il2ks/web/template_versions.json` shows the same thing.

For the details of a bump (which block was renamed, which variable added), run `uv run il2ks dev bump-templates` when you
change the file: it prints the reasons next to the file name. Put the important ones into the CHANGELOG: renamed or removed
`{% block %}`s and changed template variables break overrides most directly.

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
