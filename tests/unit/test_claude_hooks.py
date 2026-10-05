"""The Claude Code hooks (`.claude/hooks/`): the PreToolUse guard blocks what caused regressions, passes the rest."""

import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

HOOKS = Path(__file__).resolve().parents[2] / ".claude" / "hooks"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load("guard_edits")


@pytest.fixture(autouse=True)
def _no_escape_hatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The maintainer's override may be set in the shell that runs the suite (the first release's squash): the guard
    tests assert the default, so they run without it; the escape-hatch tests set it themselves."""
    monkeypatch.delenv(guard.ALLOW_ENV, raising=False)


def _fake_released(*, only: str) -> Callable[[str, str], str | None]:
    def fake(rel: str, root: str) -> str | None:
        return "origin/main" if only in rel else None

    return fake


@pytest.mark.parametrize(
    "command",
    [
        "git add -A",
        "git add . && git commit -m x",
        "git add --all",
        "git -C repo add -u",
        "git add sample_data/2026-09/x.txt",
        "git commit -am wip",
        "git commit -a -m wip",
        "git push --force origin main",
        "git push -f",
        "git commit --no-verify -m x",
        "git stash",
        "git stash pop",
        "cd x; git stash && ls",
    ],
)
def test_risky_commands_are_blocked(command: str) -> None:
    assert guard.check_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "git add src/il2ks/cli.py tests/unit/test_cli.py",
        "git add .claude/hooks/guard_edits.py",
        "git commit -m 'fix the thing'",
        "git commit --amend -m wip",
        "git stash push -u -m my-tag",
        "git stash list",
        "git push origin main",
        "git diff --stat",
        "uv run il2ks dev check",
        "ls sample_data",
    ],
)
def test_normal_commands_pass(command: str) -> None:
    assert guard.check_command(command) is None


def test_sample_data_edits_are_blocked(tmp_path: Path) -> None:
    assert guard.check_file(str(tmp_path / "sample_data" / "a.txt"), str(tmp_path))
    assert guard.check_file(str(tmp_path / "src" / "il2ks" / "cli.py"), str(tmp_path)) is None


def test_released_migrations_are_blocked_new_ones_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guard, "_released", _fake_released(only="0001_"))
    released = tmp_path / guard.MIGRATIONS / "0001_initial.py"
    fresh = tmp_path / guard.MIGRATIONS / "0002_new.py"
    message = guard.check_file(str(released), str(tmp_path))
    assert message
    assert "NEW migration" in message
    assert guard.check_file(str(fresh), str(tmp_path)) is None
    assert guard.check_file(str(tmp_path / guard.MIGRATIONS / "__init__.py"), str(tmp_path)) is None


def test_the_escape_hatch_allows_a_released_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guard, "_released", _fake_released(only=""))
    monkeypatch.setenv(guard.ALLOW_ENV, "1")
    assert guard.check_file(str(tmp_path / guard.MIGRATIONS / "0001_initial.py"), str(tmp_path)) is None


def test_released_detection_with_git(tmp_path: Path) -> None:
    """`_released` asks git whether a base ref has the file."""
    if subprocess.run(["git", "--version"], capture_output=True, check=False).returncode != 0:
        pytest.skip("git not available")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "main")
    folder = tmp_path / guard.MIGRATIONS
    folder.mkdir(parents=True)
    (folder / "0001_initial.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "m")
    assert guard._released(f"{guard.MIGRATIONS}0001_initial.py", str(tmp_path)) == "main"
    assert guard._released(f"{guard.MIGRATIONS}0002_new.py", str(tmp_path)) is None


def test_the_hook_script_exits_2_with_a_message(tmp_path: Path) -> None:
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "sample_data" / "x.txt")}}
    done = subprocess.run(
        [sys.executable, str(HOOKS / "guard_edits.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        check=False,
    )
    assert done.returncode == 2
    assert "real player data" in done.stderr


def test_the_hook_script_passes_an_ordinary_edit(tmp_path: Path) -> None:
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "src" / "a.py")}}
    done = subprocess.run(
        [sys.executable, str(HOOKS / "guard_edits.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        check=False,
    )
    assert done.returncode == 0


def test_settings_json_wires_every_hook_script() -> None:
    settings = json.loads((HOOKS.parent / "settings.json").read_text(encoding="utf-8"))
    commands = [h["command"] for group in settings["hooks"].values() for entry in group for h in entry["hooks"]]
    for script in HOOKS.glob("*.py"):
        assert any(script.name in command for command in commands), f"{script.name} is not wired in settings.json"
    for command in commands:
        assert "$CLAUDE_PROJECT_DIR" in command  # works from any cwd, in worktrees too
