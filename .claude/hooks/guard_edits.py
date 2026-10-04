"""PreToolUse hook: stop the edits and commands that caused regressions before they happen (exit 2 = blocked, the
message goes back to Claude).

Blocks:
- Edit/Write of anything under `sample_data/` (real player data) and of an already released migration (one that
  exists on `origin/main` or `main`): put the change in a NEW migration.
- Bash: `git add -A / --all / . / -u`, `git commit -a`, `git add sample_data`, force pushes, `--no-verify`, and the
  shared stash (`git stash`, `stash pop`): stage explicit paths, never skip the hooks.
Everything else passes. Never slow: a few string checks and at most two `git cat-file` calls for a migration edit.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

MIGRATIONS = "src/il2ks/db/migrations/"
BASE_REFS = ("origin/main", "main")
ALLOW_ENV = (
    "IL2KS_ALLOW_RELEASED_MIGRATION_EDIT"  # the maintainer's escape hatch (same as `il2ks dev check-migrations`)
)

BASH_RULES: tuple[tuple[str, str], ...] = (
    (
        r"\bgit\b[^;&|\n]*\badd\b[^;&|\n]*(\s-A\b|\s--all\b|\s-u\b|\s--update\b|\s\.(\s|$))",
        "stage explicit paths, not -A/./-u",
    ),
    (r"\bgit\b[^;&|\n]*\badd\b[^;&|\n]*sample_data", "sample_data/ holds real player data: never commit it"),
    (
        r"\bgit\b[^;&|\n]*\bcommit\b[^;&|\n]*(\s-[a-zA-Z]*a[a-zA-Z]*\b|\s--all\b)",
        "git commit -a stages everything: add explicit paths",
    ),
    (r"\bgit\b[^;&|\n]*\bpush\b[^;&|\n]*(\s--force\b|\s-f\b|\s--force-with-lease)", "never force-push"),
    (r"\bgit\b[^;&|\n]*\b(commit|push|merge)\b[^;&|\n]*\s--no-verify\b", "do not skip the hooks: fix what they report"),
    (
        r"\bgit\s+stash\s*($|[;&|\n])|\bgit\s+stash\s+(pop|save)\b",
        "the stash is shared by all worktrees: commit a WIP instead",
    ),
)


def _run(args: list[str], cwd: str) -> int:
    return subprocess.run(args, cwd=cwd, capture_output=True, check=False).returncode


def _relative(file_path: str, root: str) -> str:
    try:
        return Path(file_path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(file_path).as_posix()


def _released(rel: str, root: str) -> str | None:
    """The base ref that already has this migration file, if any."""
    for ref in BASE_REFS:
        if _run(["git", "cat-file", "-e", f"{ref}:{rel}"], root) == 0:
            return ref
    return None


def check_file(file_path: str, root: str) -> str | None:
    rel = _relative(file_path, root)
    if rel == "sample_data" or rel.startswith("sample_data/") or "/sample_data/" in rel:
        return "sample_data/ holds real player data: never edited or committed (fixtures: il2ks dev anonymize)."
    if rel.startswith(MIGRATIONS) and rel.endswith(".py") and not rel.endswith("__init__.py"):
        ref = _released(rel, root)
        if ref and os.environ.get(ALLOW_ENV) != "1":
            return (
                f"{rel} is already released ({ref}). Editing, splitting, renaming or deleting an applied migration "
                "breaks every existing database (InconsistentMigrationHistory). Leave it untouched and put the "
                "change in a NEW migration: `uv run il2ks manage makemigrations il2ks_db`."
            )
    return None


def check_command(command: str) -> str | None:
    for pattern, advice in BASH_RULES:
        if re.search(pattern, command):
            return advice
    return None


def main() -> int:
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input", {})
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    if payload.get("tool_name") == "Bash":
        problem = check_command(str(tool_input.get("command", "")))
    else:
        file_path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        problem = check_file(file_path, root) if file_path else None
    if problem:
        print(f"Blocked: {problem}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
