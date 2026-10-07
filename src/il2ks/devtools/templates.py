"""Developer commands for template versions (TD-25): `il2ks dev bump-templates` and `il2ks dev template-changes`.

The rules live in `il2ks.serving.templateversions`; this is the command-line side."""

import subprocess
import sys
from pathlib import Path

from il2ks.serving import templateversions as tv

REGISTRY_IN_REPO = "src/il2ks/web/" + tv.REGISTRY_NAME


def bump_templates(*, check: bool, root: Path | None = None, major: list[str] | None = None) -> int:
    """Add missing version lines, raise the version of changed files (N when the override contract changed, else M),
    rewrite the registry. With `check`, change nothing and exit 1 when something would change (for CI and
    pre-commit). `major`: force N for the changed files named (an empty list: all changed files)."""
    actions = tv.bump_templates(root, write=not check, major=major)
    if not actions:
        print("template versions are in order")
        return 0
    verb = "would change" if check else "changed"
    print(f"{verb} {len(actions)} file(s):")
    for action in actions:
        print(f"  {tv.describe(action)}")
    if check:
        print("run `il2ks dev bump-templates` and commit the result")
        return 1
    breaking = [a for a in actions if a.part == "major"]
    if breaking:
        print(f"{len(breaking)} file(s) got a new N (breaking for overrides): name them in the CHANGELOG")
    print("commit the files and src/il2ks/web/template_versions.json together")
    return 0


def template_changes(old_tag: str, root: Path | None = None, *, everything: bool = False) -> int:
    """List what overrides must look at since a release tag (for the release notes): files whose N went up and removed
    files; `everything` adds the minor-only bumps and new files. Taken from the registry file as it was at that tag
    (`git show <tag>:src/il2ks/web/template_versions.json`); a 0.1.0 registry has single numbers, read as N.0."""
    try:
        shown = subprocess.run(
            ["git", "show", f"{old_tag}:{REGISTRY_IN_REPO}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError as exc:
        print(f"il2ks dev template-changes: cannot run git ({exc})", file=sys.stderr)
        return 2
    if shown.returncode != 0:
        print(
            f"il2ks dev template-changes: git has no {REGISTRY_IN_REPO} at {old_tag!r} ({shown.stderr.strip()}).\n"
            "Releases before template versions existed changed every page: say so in the notes.",
            file=sys.stderr,
        )
        return 2
    lines = tv.registry_changes(tv.parse_registry(shown.stdout), tv.load_registry(root), everything=everything)
    if not lines:
        what = "changed" if everything else "changed its override contract (no N bump)"
        print(f"no template or stylesheet {what} since {old_tag}")
        return 0
    print(f"Templates and static files with a new N since {old_tag} (overrides based on the old versions need a look):")
    for line in lines:
        print(f"- {line}")
    return 0
