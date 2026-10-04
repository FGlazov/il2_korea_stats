"""`il2ks dev check`: everything to run before committing, in one command. The single source of truth for agents, the
Stop hook, pre-commit and CI (they all call this, so they cannot drift apart).

Tiers (each includes the one before):
- `fast`  (Stop hook, ~15 s): static checks + the guards + the guard tests. No full test suite.
- `quick` (default, before every commit): fast + the whole unit suite.
- `full`  (before merging / finishing a task): quick + integration tests (the page tests with their query budgets).
Extras: `--postgres` (needs `docker compose -f docker/compose.dev.yaml up -d db`) and `--e2e` (needs Playwright).

Independent steps run in parallel; every failure is reported with the command that fixes it. Tools are taken from the
interpreter's own environment, so `uv run il2ks dev check` and a plain activated venv behave the same.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Literal

Tier = Literal["fast", "quick", "full"]
TIERS: tuple[Tier, ...] = ("fast", "quick", "full")
Kind = Literal["static", "guard", "test"]

# Fast tests that carry the regression guards: template compile, translations, template versions, migration graph.
GUARD_TESTS = (
    "tests/unit/test_template_compile.py",
    "tests/unit/test_migration_guards.py",
    "tests/unit/test_translations.py",
    "tests/unit/test_i18n_templates.py",
    "tests/unit/test_template_versions.py",
)
TAIL_LINES = 60


@dataclass(frozen=True)
class Step:
    name: str
    kind: Kind
    argv: tuple[str, ...]  # "{py}" = this interpreter, "tool:<name>" = an executable of this environment
    fix: str  # what to do when it fails
    tiers: tuple[Tier, ...] = TIERS
    extra: bool = False  # only with its flag (--postgres, --e2e)
    env: tuple[tuple[str, str], ...] = ()


def _workers() -> str:
    return str(max(2, min(8, (os.cpu_count() or 4) // 2)))


def steps() -> list[Step]:
    il2ks = ("{py}", "-m", "il2ks")
    unit = ("{py}", "-m", "pytest", "tests/unit", "-q", "-n", _workers(), "--dist", "worksteal")
    return [
        Step(
            "ruff-check", "static", ("tool:ruff", "check"), "uv run ruff check --fix   (then fix what is left by hand)"
        ),
        Step("ruff-format", "static", ("tool:ruff", "format", "--check"), "uv run ruff format"),
        Step(
            "pyright", "static", ("tool:pyright",), "fix the types (strict, no Any); `uv run pyright <file>` is faster"
        ),
        Step("lint-imports", "static", ("tool:lint-imports",), "core must not import Django or outer layers"),
        Step(
            "vulture",
            "static",
            ("tool:vulture",),
            "delete the dead code, or list a deliberate leftover in vulture_whitelist.py with a reason",
        ),
        Step(
            "makemigrations",
            "guard",
            (*il2ks, "manage", "makemigrations", "--check", "--dry-run"),
            "uv run il2ks manage makemigrations il2ks_db   (a model changed without a migration)",
        ),
        Step(
            "migrations",
            "guard",
            (*il2ks, "dev", "check-migrations"),
            "released migrations are frozen: put the change in a NEW migration; one leaf, no duplicate numbers",
        ),
        Step(
            "template-versions",
            "guard",
            (*il2ks, "dev", "bump-templates", "--check"),
            "uv run il2ks dev bump-templates   (and commit the result)",
        ),
        Step(
            "translations",
            "guard",
            (*il2ks, "dev", "translations", "check"),
            "uv run il2ks dev translations update   (and commit the .po/.mo files)",
        ),
        Step(
            "guard-tests",
            "test",
            ("{py}", "-m", "pytest", *GUARD_TESTS, "-q", "-p", "no:cacheprovider", "-n", "4"),
            "template compile / translations / template versions / migration graph: read the failure",
            tiers=("fast",),
        ),
        Step(
            "unit-tests",
            "test",
            unit,
            "uv run pytest tests/unit -q -x   (then fix the failing test or the code)",
            ("quick", "full"),
        ),
        Step(
            "integration-tests",
            "test",
            ("{py}", "-m", "pytest", "tests/integration", "-q", "-n", _workers(), "--dist", "worksteal"),
            "uv run pytest tests/integration -q -x   (page tests also guard the query budgets)",
            ("full",),
        ),
        Step(
            "postgres-tests",
            "test",
            ("{py}", "-m", "pytest", "-q", "-n", "2"),
            "needs `docker compose -f docker/compose.dev.yaml up -d db`; then IL2KS_TEST_DB=postgres uv run pytest -x",
            ("full",),
            extra=True,
            env=(("IL2KS_TEST_DB", "postgres"),),
        ),
        Step(
            "e2e-tests",
            "test",
            ("{py}", "-m", "pytest", "-m", "e2e", "-q"),
            "uv run playwright install chromium; IL2KS_TEST_E2E=1 uv run pytest -m e2e -x",
            ("full",),
            extra=True,
            env=(("IL2KS_TEST_E2E", "1"),),
        ),
    ]


@dataclass
class Result:
    step: Step
    code: int
    seconds: float
    output: str


def _resolve(argv: Sequence[str]) -> list[str] | None:
    """Replace the placeholders; None when a tool is not installed."""
    out: list[str] = []
    for part in argv:
        if part == "{py}":
            out.append(sys.executable)
        elif part.startswith("tool:"):
            found = shutil.which(part[5:], path=str(Path(sys.executable).parent))
            if found is None:
                return None
            out.append(found)
        else:
            out.append(part)
    return out


def run_step(step: Step, root: Path) -> Result:
    started = time.monotonic()
    argv = _resolve(step.argv)
    if argv is None:
        return Result(step, 2, 0.0, f"{step.argv[0][5:]} is not installed in this environment: run `uv sync`")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1", **dict(step.env)}
    env.pop("VIRTUAL_ENV", None)
    done = subprocess.run(
        argv, cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    return Result(step, done.returncode, time.monotonic() - started, (done.stdout + done.stderr).strip())


def select(tier: Tier, *, no_tests: bool, postgres: bool, e2e: bool, only: Sequence[str] = ()) -> list[Step]:
    chosen: list[Step] = []
    for step in steps():
        if only:
            if step.name in only:
                chosen.append(step)
            continue
        if no_tests and step.kind == "test":
            continue
        if step.extra:
            if not ((step.name == "postgres-tests" and postgres) or (step.name == "e2e-tests" and e2e)):
                continue
        elif tier not in step.tiers:
            continue
        chosen.append(step)
    return chosen


def repo_root() -> Path:
    here = Path.cwd()
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=here, capture_output=True, text=True, check=False
    ).stdout.strip()
    return Path(top) if top else here


def _git_out(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", check=False)
    return done.stdout


def fingerprint(root: Path, tier: str) -> str:
    """Hash of HEAD + every uncommitted change (tracked diff, untracked files by size and mtime)."""
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(tier.encode())
    digest.update(_git_out(root, "rev-parse", "HEAD").encode())
    digest.update(_git_out(root, "diff", "HEAD").encode())
    for line in _git_out(root, "ls-files", "--others", "--exclude-standard").splitlines():
        path = root / line
        if path.is_file():
            stat = path.stat()
            digest.update(f"{line}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


def _stamp_path(root: Path, tier: str) -> Path | None:
    git_dir = _git_out(root, "rev-parse", "--absolute-git-dir").strip()
    return Path(git_dir) / f"il2ks-check-ok-{tier}" if git_dir else None


def fix_first(root: Path) -> None:
    """`--fix`: the generators and auto-fixers, so the checks that follow only see what needs a human."""
    for label, argv in (
        ("ruff check --fix", ["tool:ruff", "check", "--fix", "--quiet"]),
        ("ruff format", ["tool:ruff", "format", "--quiet"]),
        ("bump-templates", ["{py}", "-m", "il2ks", "dev", "bump-templates"]),
        ("translations update", ["{py}", "-m", "il2ks", "dev", "translations", "update"]),
    ):
        resolved = _resolve(argv)
        if resolved is None:
            continue
        env = {**os.environ, "PYTHONUTF8": "1"}
        env.pop("VIRTUAL_ENV", None)
        done = subprocess.run(resolved, cwd=root, env=env, capture_output=True, text=True, check=False)
        print(f"fix: {label}: {'ok' if done.returncode == 0 else 'left problems for you'}")


def check(
    tier: Tier = "quick",
    *,
    fix: bool = False,
    no_tests: bool = False,
    postgres: bool = False,
    e2e: bool = False,
    only: Sequence[str] = (),
    if_changed: bool = False,
    verbose: bool = False,
    list_only: bool = False,
) -> int:
    root = repo_root()
    chosen = select(tier, no_tests=no_tests, postgres=postgres, e2e=e2e, only=only)
    if list_only:
        for step in chosen:
            print(f"{step.name:<20}{step.kind:<8}{' '.join(step.argv)}")
        return 0
    if only and not chosen:
        print(f"no such step: {', '.join(only)} (see --list)")
        return 2
    stamp = _stamp_path(root, tier) if if_changed and not only and not no_tests else None
    if stamp is not None and stamp.is_file() and stamp.read_text(encoding="utf-8") == fingerprint(root, tier):
        print(f"il2ks check ({tier}): nothing changed since the last passing run")
        return 0
    if fix:
        fix_first(root)
    before = fingerprint(root, tier) if stamp is not None else ""
    started = time.monotonic()
    print(f"il2ks check ({tier}): {len(chosen)} steps, running in parallel ...", flush=True)
    with ThreadPoolExecutor(max_workers=len(chosen)) as pool:
        results = list(pool.map(partial(run_step, root=root), chosen))
    failed = [r for r in results if r.code != 0]
    for r in results:
        print(f"  {'ok  ' if r.code == 0 else 'FAIL'} {r.step.name:<20}{r.seconds:6.1f}s")
    for r in failed:
        lines = r.output.splitlines()
        shown = lines if verbose else lines[-TAIL_LINES:]
        print(f"\n--- {r.step.name} failed (exit {r.code}) ---")
        if len(shown) < len(lines):
            print(f"[... {len(lines) - len(shown)} earlier lines hidden; --verbose shows everything]")
        print("\n".join(shown))
        print(f"fix: {r.step.fix}")
    total = time.monotonic() - started
    if failed:
        print(f"\nil2ks check ({tier}) FAILED: {', '.join(r.step.name for r in failed)} ({total:.0f}s)")
        print("Fix it, then run: uv run il2ks dev check" + ("" if tier == "quick" else f" --{tier}"))
        print("Auto-fixable parts (format, lint, template versions, translations): uv run il2ks dev check --fix")
        return 1
    print(f"\nil2ks check ({tier}) passed in {total:.0f}s")
    if stamp is not None:
        stamp.write_text(before, encoding="utf-8")
    return 0
