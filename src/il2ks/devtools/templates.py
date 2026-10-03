"""Developer commands for template versions (TD-25): `il2ks dev bump-templates` and `il2ks dev template-changes`.

The rules live in `il2ks.serving.templateversions`; this is the command-line side."""

import subprocess
import sys
from pathlib import Path

from il2ks.serving import templateversions as tv

REGISTRY_IN_REPO = "src/il2ks/web/" + tv.REGISTRY_NAME


def bump_templates(*, check: bool, root: Path | None = None) -> int:
    """Add missing version lines, raise the version of changed files, rewrite the registry. With `check`, change
    nothing and exit 1 when something would change (for CI and pre-commit)."""
    actions = tv.bump_templates(root, write=not check)
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
    print("commit the files and src/il2ks/web/template_versions.json together")
    return 0


def template_changes(old_tag: str, root: Path | None = None) -> int:
    """List the template versions that changed since a release tag (for the release notes), from the registry file
    as it was at that tag (`git show <tag>:src/il2ks/web/template_versions.json`)."""
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
    lines = tv.registry_changes(tv.parse_registry(shown.stdout), tv.load_registry(root))
    if not lines:
        print(f"no template or stylesheet changed since {old_tag}")
        return 0
    print(f"Templates and static files that changed since {old_tag} (overrides based on the old versions need a look):")
    for line in lines:
        print(f"- {line}")
    return 0
