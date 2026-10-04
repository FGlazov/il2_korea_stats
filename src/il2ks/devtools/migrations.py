"""Guards for the migration history: `il2ks dev check-migrations` (part of `il2ks dev check`, pre-commit and CI).

Two regressions this prevents (both happened):
- **Editing, renaming, splitting or deleting a migration that is already released.** A database that applied the old
  migration then fails with InconsistentMigrationHistory. A migration counts as released when it exists in the history
  the branch grew from: `origin/main` or the local `main` (merge-base with HEAD). Only *adding* files is allowed.
- **Two leaf migrations / duplicate numbers**, which parallel agent branches produce (both add `0020_...` on top of
  `0019`). Whoever merges second renumbers their migration after the latest one on main and fixes its dependency.

The graph is read statically (AST), so the check needs neither Django nor a database and runs on any directory.
"""

import ast
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

APP_LABEL = "il2ks_db"
MIGRATIONS_DIR = "src/il2ks/db/migrations"
BASE_REFS = ("origin/main", "main")
ALLOW_ENV = "IL2KS_ALLOW_RELEASED_MIGRATION_EDIT"  # maintainer-only escape hatch, never for agents
NUMBER = re.compile(r"^(\d{4})_")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", check=False)


def migration_names(directory: Path) -> list[str]:
    """Names of the migrations in a folder (file stems), sorted."""
    return sorted(p.stem for p in directory.glob("[0-9]*.py"))


def duplicate_numbers(names: list[str]) -> dict[str, list[str]]:
    """Migration numbers used by more than one file: {"0020": ["0020_a", "0020_b"]}."""
    by_number: dict[str, list[str]] = {}
    for name in names:
        match = NUMBER.match(name)
        if match:
            by_number.setdefault(match.group(1), []).append(name)
    return {number: found for number, found in by_number.items() if len(found) > 1}


def dependencies_of(path: Path, app_label: str = APP_LABEL) -> list[str]:
    """The same-app migrations a migration file depends on, read from `dependencies = [...]` without importing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "dependencies" for t in node.targets
        ):
            for item in ast.walk(node.value):
                if isinstance(item, ast.Tuple) and len(item.elts) == 2:
                    app, name = item.elts
                    if (
                        isinstance(app, ast.Constant)
                        and isinstance(name, ast.Constant)
                        and app.value == app_label
                        and isinstance(name.value, str)
                    ):
                        found.append(name.value)
    return found


def graph_problems(directory: Path, app_label: str = APP_LABEL) -> list[str]:
    """Duplicate numbers, dangling dependencies and more than one leaf. Empty when the history is a single chain tip."""
    names = migration_names(directory)
    problems: list[str] = []
    for number, found in duplicate_numbers(names).items():
        problems.append(
            f"number {number} is used by {', '.join(found)}: renumber the newer one after the latest migration on main "
            "and point its dependency at that migration"
        )
    depended_on: set[str] = set()
    for name in names:
        for dep in dependencies_of(directory / f"{name}.py", app_label):
            depended_on.add(dep)
            if dep not in names:
                problems.append(f"{name} depends on {dep}, which does not exist")
    leaves = [n for n in names if n not in depended_on]
    if len(leaves) > 1:
        problems.append(
            f"{len(leaves)} leaf migrations ({', '.join(leaves)}): the history must end in exactly one. "
            "Make the newer migration depend on the latest one (or add a merge migration if both are already released)"
        )
    if names and not leaves:
        problems.append("no leaf migration: the dependencies form a cycle")
    return problems


@dataclass(frozen=True)
class Released:
    """A released migration file that the working tree changed."""

    path: str
    status: str  # git status letter: M modified, D deleted (or renamed away), T type change
    base: str  # the ref it is released in


def resolve_bases(root: Path, refs: tuple[str, ...] = BASE_REFS) -> list[str]:
    """The refs from `refs` that exist in this repository."""
    return [r for r in refs if _git(root, "rev-parse", "--verify", "--quiet", r + "^{commit}").returncode == 0]


def changed_released(root: Path, base_refs: list[str], directory: str = MIGRATIONS_DIR) -> list[Released]:
    """Migration files that exist at the merge-base with each base ref and were modified, deleted or renamed since
    (committed or not). Newly added files are fine and never listed."""
    found: dict[str, Released] = {}
    for ref in base_refs:
        merge_base = _git(root, "merge-base", "HEAD", ref)
        if merge_base.returncode != 0:
            continue
        diff = _git(root, "diff", "--name-status", "--no-renames", merge_base.stdout.strip(), "--", directory)
        for line in diff.stdout.splitlines():
            status, _, path = line.partition("\t")
            if status != "A" and path.endswith(".py") and Path(path).name != "__init__.py":
                found.setdefault(path, Released(path, status, ref))
    return sorted(found.values(), key=lambda r: r.path)


def released_problems(root: Path, base_refs: list[str] | None = None) -> list[str]:
    refs = resolve_bases(root) if base_refs is None else base_refs
    return [
        f"{r.path} is released ({r.base}) but {'deleted/renamed' if r.status == 'D' else 'modified'}: "
        "restore it (`git checkout <base> -- <path>`) and put the change in a NEW migration"
        for r in changed_released(root, refs)
    ]


def check(root: Path, *, base_refs: list[str] | None = None) -> int:
    """Run both guards, print the findings, return the exit code (0 fine, 1 problems)."""
    problems = graph_problems(root / MIGRATIONS_DIR)
    refs = resolve_bases(root) if base_refs is None else base_refs
    if refs:
        if os.environ.get(ALLOW_ENV) != "1":
            problems += released_problems(root, refs)
    elif os.environ.get("CI"):
        problems.append(
            f"no base ref ({' or '.join(BASE_REFS)}) to compare with: check out with full history (fetch-depth: 0)"
        )
    else:
        print(f"note: neither {' nor '.join(BASE_REFS)} exists here; released-migration check skipped")
    for problem in problems:
        print(f"  - {problem}")
    if problems:
        print(
            "Never edit, split, rename or delete a migration that is already on main: databases that applied it fail "
            "with InconsistentMigrationHistory."
        )
        return 1
    print(f"migrations are in order (single leaf, none of the released ones changed; base: {', '.join(refs) or '-'})")
    return 0
