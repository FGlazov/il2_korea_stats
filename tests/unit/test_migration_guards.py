"""The migration guards of `il2ks dev check-migrations`: released migrations are frozen, the history has exactly one
leaf and no duplicate numbers (an agent once split an applied migration: InconsistentMigrationHistory on every
existing database)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from il2ks.devtools import migrations as guard

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")

REAL = Path(__file__).resolve().parents[2] / guard.MIGRATIONS_DIR


def _migration(depends_on: str | None) -> str:
    deps = f'[("il2ks_db", "{depends_on}")]' if depends_on else "[]"
    head = "from django.db import migrations\n\n\nclass Migration(migrations.Migration):\n"
    return f"{head}    dependencies = {deps}\n    operations = []\n"


def _write(folder: Path, name: str, depends_on: str | None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.py").write_text(_migration(depends_on), encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def template_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A repo whose `main` has migrations 0001 <- 0002, with a feature branch checked out (git is slow: built once)."""
    root = tmp_path_factory.mktemp("template_repo")
    _git(root, "init", "-q", "-b", "main")
    folder = root / guard.MIGRATIONS_DIR
    _write(folder, "0001_initial", None)
    _write(folder, "0002_second", "0001_initial")
    (folder / "__init__.py").write_text("", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "main")
    _git(root, "checkout", "-q", "-b", "feature")
    return root


@pytest.fixture
def repo(template_repo: Path, tmp_path: Path) -> Path:
    """A private copy of the template repo for one test."""
    target = tmp_path / "repo"
    shutil.copytree(template_repo, target)
    return target


def _problems(root: Path) -> list[str]:
    return guard.released_problems(root, guard.resolve_bases(root))


# --- the real history -------------------------------------------------------------------------------------------------
def test_the_real_history_is_a_single_chain() -> None:
    assert guard.graph_problems(REAL) == []


# --- single leaf / numbers -------------------------------------------------------------------------------------------
def test_a_clean_chain_has_no_problems(repo: Path) -> None:
    assert guard.graph_problems(repo / guard.MIGRATIONS_DIR) == []


def test_a_parallel_branch_with_the_same_number_is_flagged(repo: Path) -> None:
    folder = repo / guard.MIGRATIONS_DIR
    _write(folder, "0003_a", "0002_second")
    _write(folder, "0003_b", "0002_second")  # two agents, same parent
    problems = guard.graph_problems(folder)
    assert any("number 0003" in p for p in problems)
    assert any("2 leaf migrations" in p for p in problems)


def test_two_leaves_with_different_numbers_are_flagged(repo: Path) -> None:
    folder = repo / guard.MIGRATIONS_DIR
    _write(folder, "0003_a", "0002_second")
    _write(folder, "0004_b", "0002_second")
    assert any("2 leaf migrations" in p for p in guard.graph_problems(folder))


def test_a_dangling_dependency_is_flagged(repo: Path) -> None:
    folder = repo / guard.MIGRATIONS_DIR
    _write(folder, "0003_a", "0002_gone")
    assert any("0002_gone, which does not exist" in p for p in guard.graph_problems(folder))


def test_dependencies_on_other_apps_are_ignored(repo: Path) -> None:
    folder = repo / guard.MIGRATIONS_DIR
    (folder / "0003_x.py").write_text(
        _migration("0002_second").replace('")]', '"), ("auth", "0001_initial")]'), encoding="utf-8"
    )
    assert guard.dependencies_of(folder / "0003_x.py") == ["0002_second"]
    assert guard.graph_problems(folder) == []


# --- released migrations are frozen ----------------------------------------------------------------------------------
def test_adding_a_migration_is_fine(repo: Path) -> None:
    _write(repo / guard.MIGRATIONS_DIR, "0003_new", "0002_second")
    assert _problems(repo) == []
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add")
    assert _problems(repo) == []


def test_editing_a_released_migration_is_flagged_even_uncommitted(repo: Path) -> None:
    target = repo / guard.MIGRATIONS_DIR / "0002_second.py"
    target.write_text(target.read_text(encoding="utf-8") + "# tweak\n", encoding="utf-8")
    assert any("0002_second.py is released" in p for p in _problems(repo))


def test_editing_a_released_migration_is_flagged_once_committed(repo: Path) -> None:
    target = repo / guard.MIGRATIONS_DIR / "0002_second.py"
    target.write_text(target.read_text(encoding="utf-8") + "# tweak\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "edit")
    assert len(_problems(repo)) == 1


def test_splitting_or_renaming_a_released_migration_is_flagged(repo: Path) -> None:
    folder = repo / guard.MIGRATIONS_DIR
    _git(repo, "mv", str(folder / "0002_second.py"), str(folder / "0002_renamed.py"))
    _write(folder, "0003_rest", "0002_renamed")
    problems = _problems(repo)
    assert any("0002_second.py is released" in p and "deleted/renamed" in p for p in problems)


def test_deleting_a_released_migration_is_flagged(repo: Path) -> None:
    (repo / guard.MIGRATIONS_DIR / "0002_second.py").unlink()
    assert any("deleted/renamed" in p for p in _problems(repo))


def test_a_migration_added_on_the_branch_may_still_change(repo: Path) -> None:
    folder = repo / guard.MIGRATIONS_DIR
    _write(folder, "0003_new", "0002_second")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add")
    (folder / "0003_new.py").write_text(_migration("0002_second") + "# reworked\n", encoding="utf-8")
    assert _problems(repo) == []


def test_main_moving_ahead_is_not_a_change_of_the_branch(repo: Path) -> None:
    """Another branch landed 0003 on main after we branched: that is not our edit, nor a deletion."""
    _git(repo, "checkout", "-q", "main")
    _write(repo / guard.MIGRATIONS_DIR, "0003_other", "0002_second")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "other")
    _git(repo, "checkout", "-q", "feature")
    assert _problems(repo) == []


def test_check_reports_and_returns_one(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / guard.MIGRATIONS_DIR / "0001_initial.py").write_text("# gone\n", encoding="utf-8")
    assert guard.check(repo) == 1
    assert "InconsistentMigrationHistory" in capsys.readouterr().out


def test_check_passes_on_a_clean_repo(repo: Path) -> None:
    assert guard.check(repo) == 0


def test_the_escape_hatch_is_honoured(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / guard.MIGRATIONS_DIR / "0001_initial.py").write_text("# gone\n", encoding="utf-8")
    monkeypatch.setenv(guard.ALLOW_ENV, "1")
    assert guard.check(repo) == 0
