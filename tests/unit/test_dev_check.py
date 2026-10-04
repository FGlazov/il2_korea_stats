"""`il2ks dev check` (the one pre-commit command) and `il2ks dev translations check`."""

import sys
from pathlib import Path

import pytest

from il2ks.devtools import check as dev_check
from il2ks.devtools import translations

DE: translations.LanguageSpec = ("de", "de", "nplurals=2; plural=(n != 1);")


def names(tier: dev_check.Tier, *, postgres: bool = False, e2e: bool = False) -> list[str]:
    return [s.name for s in dev_check.select(tier, no_tests=False, postgres=postgres, e2e=e2e)]


# --- tiers ---
def test_every_regression_guard_is_in_every_tier() -> None:
    guards = {"ruff-check", "ruff-format", "pyright", "lint-imports", "vulture", "makemigrations"}
    guards |= {"migrations", "template-versions", "translations"}
    for tier in dev_check.TIERS:
        assert guards <= set(names(tier)), tier


def test_tiers_grow() -> None:
    assert "guard-tests" in names("fast")
    assert "unit-tests" not in names("fast")
    assert "unit-tests" in names("quick")
    assert "integration-tests" not in names("quick")
    assert "integration-tests" in names("full")
    assert "perf-tests" not in names("quick")
    assert "perf-tests" in names("full")


def test_extras_need_their_flag() -> None:
    assert "postgres-tests" not in names("full")
    assert "postgres-tests" in names("full", postgres=True)
    assert "e2e-tests" in names("full", e2e=True)


def test_no_tests_leaves_only_static_checks_and_guards() -> None:
    assert not {s.kind for s in dev_check.select("quick", no_tests=True, postgres=False, e2e=False)} & {"test"}


def test_only_picks_named_steps() -> None:
    assert [s.name for s in dev_check.select("fast", no_tests=False, postgres=False, e2e=False, only=["pyright"])] == [
        "pyright"
    ]


def test_every_step_says_how_to_fix_it() -> None:
    assert all(step.fix for step in dev_check.steps())


# --- running ---
def test_a_failing_step_reports_output_and_code(tmp_path: Path) -> None:
    step = dev_check.Step("boom", "guard", ("{py}", "-c", "import sys; print('bad thing'); sys.exit(3)"), "do X")
    result = dev_check.run_step(step, tmp_path)
    assert result.code == 3
    assert "bad thing" in result.output


def test_a_passing_step(tmp_path: Path) -> None:
    result = dev_check.run_step(dev_check.Step("ok", "guard", ("{py}", "-c", "pass"), ""), tmp_path)
    assert result.code == 0


def test_a_missing_tool_is_reported_not_raised(tmp_path: Path) -> None:
    result = dev_check.run_step(dev_check.Step("x", "static", ("tool:no-such-tool-anywhere",), ""), tmp_path)
    assert result.code == 2
    assert "uv sync" in result.output


def test_tools_come_from_this_environment() -> None:
    resolved = dev_check._resolve(["tool:ruff"])  # pyright: ignore[reportPrivateUsage]
    assert resolved is not None
    assert Path(resolved[0]).parent == Path(sys.executable).parent


# --- translations check ---
@pytest.fixture
def locale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(translations, "LOCALE_DIR", tmp_path)
    return tmp_path


def test_check_is_clean_after_update_and_compile(locale: Path) -> None:
    translations.update([DE])
    translations.compile_all([DE])
    assert translations.check([DE]) == []


def test_check_notices_a_missing_string_and_a_stale_mo(locale: Path) -> None:
    translations.update([DE])
    translations.import_drafts("de", {"Players": "Spieler"})
    translations.compile_all([DE])
    assert translations.check([DE]) == []

    translations.import_drafts("de", {"Mission": "Einsatz"})  # .po changed, .mo not recompiled
    assert any("django.mo is stale" in p for p in translations.check([DE]))

    catalog = translations.read_catalog("de")
    catalog.add("A brand new string in the templates")
    translations.po_path("de").write_bytes(b"")  # a damaged/empty po is just "out of date"
    assert any("out of date" in p for p in translations.check([DE]))


def test_check_accepts_windows_line_endings(locale: Path) -> None:
    translations.update([DE])
    translations.compile_all([DE])
    path = translations.po_path("de")
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))  # what git's autocrlf does on a Windows checkout
    assert translations.check([DE]) == []


def test_check_reports_a_missing_catalog(locale: Path) -> None:
    assert any("is missing" in p for p in translations.check([DE]))
