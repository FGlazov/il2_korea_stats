"""`custom/` overrides (TD-25, FR-ADM-6): copy, list, diff, accept, version detection, startup warning, doctor check."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from il2ks.cli import EXIT_OK, EXIT_USAGE, main
from il2ks.config import Config, load_config
from il2ks.exitcodes import EXIT_PROBLEMS
from il2ks.ops import serving_checks
from il2ks.ops.doctor import Level
from il2ks.serving import custom, templateversions
from il2ks.serving.templateversions import with_header

HOME = "templates/il2ks/home.html"
SITE_CSS = "static/css/site.css"


def write_builtin(root: Path, key: str, body: str, version: int | None) -> Path:
    path = root / key.partition("/")[2]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if version is None else with_header(body, key, version), encoding="utf-8")
    return path


@pytest.fixture
def builtin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[custom.Kind, Path]]:
    """Two fake built-in roots, so tests don't depend on the real templates."""
    roots: dict[custom.Kind, Path] = {
        "templates": tmp_path / "pkg" / "templates",
        "static": tmp_path / "pkg" / "static",
    }
    write_builtin(roots["templates"], HOME, "<h1>home v1</h1>\n", 1)
    write_builtin(roots["static"], SITE_CSS, "body {}\n", 1)
    write_builtin(roots["static"], "static/vendor/lib.js", "var lib;\n", None)  # vendored: has no version
    (roots["templates"] / "both.txt").write_text("t", encoding="utf-8")
    (roots["static"] / "both.txt").write_text("s", encoding="utf-8")

    def fake_roots(kind: custom.Kind) -> list[Path]:
        return [roots[kind]]

    monkeypatch.setattr(custom, "builtin_roots", fake_roots)
    custom.startup_scan.cache_clear()
    yield roots
    custom.startup_scan.cache_clear()


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return load_config(None, {"IL2KS_DATA_DIR": str(tmp_path / "data")}, create_server_uid=False)


def place(cfg: Config, key: str, content: str) -> Path:
    path = cfg.data_dir / "custom" / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def states(cfg: Config) -> dict[str, str]:
    return {c.key: c.state for c in custom.override_checks(cfg)}


def upgrade_home(builtin: dict[custom.Kind, Path], version: int = 2) -> None:
    write_builtin(builtin["templates"], HOME, f"<h1>home v{version}</h1>\n", version)


# --- copy ------------------------------------------------------------------------------------------------------------


def test_copy_a_template_by_bare_name_keeps_the_version_line(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    placed = custom.copy_builtin(cfg, "il2ks/home.html")
    target = cfg.data_dir / "custom" / HOME
    assert placed.override == target
    assert target.read_text(encoding="utf-8").startswith("{# il2ks-template: templates/il2ks/home.html v1 ")
    assert target.read_bytes() == (builtin["templates"] / "il2ks" / "home.html").read_bytes()
    record = json.loads((cfg.data_dir / "custom" / ".il2ks-overrides.json").read_text(encoding="utf-8"))
    entry = record["overrides"][HOME]
    assert entry["original_sha256"] == custom.sha256_of(builtin["templates"] / "il2ks" / "home.html")
    assert entry["template_version"] == "1"
    assert entry["il2ks_version"]


def test_copy_a_static_file_with_a_prefix_and_windows_slashes(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    custom.copy_builtin(cfg, "static\\css\\site.css")
    assert (cfg.data_dir / "custom" / "static" / "css" / "site.css").is_file()


def test_a_name_that_is_both_kinds_must_be_prefixed(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    with pytest.raises(custom.CustomError, match="template and as a static file"):
        custom.copy_builtin(cfg, "both.txt")
    custom.copy_builtin(cfg, "static/both.txt")
    assert (cfg.data_dir / "custom" / "static" / "both.txt").read_text(encoding="utf-8") == "s"


def test_copy_refuses_to_overwrite_your_edits_without_force(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    custom.copy_builtin(cfg, "il2ks/home.html")
    target = cfg.data_dir / "custom" / HOME
    target.write_text("my edit", encoding="utf-8")
    with pytest.raises(custom.CustomError, match="--force"):
        custom.copy_builtin(cfg, "il2ks/home.html")
    assert target.read_text(encoding="utf-8") == "my edit"
    custom.copy_builtin(cfg, "il2ks/home.html", force=True)
    assert "home v1" in target.read_text(encoding="utf-8")


@pytest.mark.parametrize("bad", ["../secret.txt", "/etc/passwd", "templates/../../x", "", "nope/missing.html"])
def test_unknown_or_unsafe_paths_are_refused(cfg: Config, builtin: dict[custom.Kind, Path], bad: str) -> None:
    with pytest.raises(custom.CustomError):
        custom.copy_builtin(cfg, bad)
    assert not (cfg.data_dir / "custom").exists()


# --- the states ------------------------------------------------------------------------------------------------------


def test_a_fresh_copy_is_current(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    custom.copy_builtin(cfg, "il2ks/home.html")
    custom.copy_builtin(cfg, "css/site.css")
    assert states(cfg) == {HOME: "current", SITE_CSS: "current"}


def test_an_upgrade_makes_a_copy_outdated_and_names_both_versions(
    cfg: Config, builtin: dict[custom.Kind, Path]
) -> None:
    custom.copy_builtin(cfg, "il2ks/home.html")
    upgrade_home(builtin, 3)
    (check,) = custom.override_checks(cfg)
    assert check.state == "outdated"
    assert (check.override_version, check.builtin_version) == (1, 3)
    assert "version 1" in check.message
    assert "version 3" in check.message
    assert "il2ks custom diff templates/il2ks/home.html" in check.fix
    assert check.is_problem


def test_your_own_edits_do_not_count_as_an_old_version(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    placed = custom.copy_builtin(cfg, "il2ks/home.html")
    placed.override.write_text(placed.override.read_text(encoding="utf-8") + "<p>mine</p>\n", encoding="utf-8")
    assert states(cfg) == {HOME: "current"}


def test_a_hand_copy_with_a_version_line_is_checked_without_any_record(
    cfg: Config, builtin: dict[custom.Kind, Path]
) -> None:
    place(cfg, HOME, with_header("<h1>mine</h1>\n", HOME, 1))
    assert states(cfg) == {HOME: "current"}
    upgrade_home(builtin)
    assert states(cfg) == {HOME: "outdated"}


def test_a_file_without_a_version_line_is_unversioned(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    place(cfg, HOME, "by hand")
    (check,) = custom.override_checks(cfg)
    assert check.state == "unversioned"
    assert check.override_version is None
    assert check.builtin_version == 1
    assert check.is_problem


def test_a_deleted_version_line_is_forgiven_when_the_record_shows_the_current_original(
    cfg: Config, builtin: dict[custom.Kind, Path]
) -> None:
    placed = custom.copy_builtin(cfg, "il2ks/home.html")
    placed.override.write_text("<h1>my own page</h1>", encoding="utf-8")  # header gone
    assert states(cfg) == {HOME: "current"}


def test_a_deleted_version_line_uses_the_version_from_the_copy_record(
    cfg: Config, builtin: dict[custom.Kind, Path]
) -> None:
    placed = custom.copy_builtin(cfg, "il2ks/home.html")
    placed.override.write_text("<h1>my own page</h1>", encoding="utf-8")
    upgrade_home(builtin, 2)
    (check,) = custom.override_checks(cfg)
    assert check.state == "outdated"
    assert (check.override_version, check.builtin_version) == (1, 2)


def test_a_file_that_replaces_nothing_is_fine(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    place(cfg, "static/my-banner.png", "png")
    place(cfg, "templates/my/page.html", "<p>mine</p>")
    place(cfg, "static/.DS_Store", "junk")
    assert states(cfg) == {"static/my-banner.png": "custom-only", "templates/my/page.html": "custom-only"}
    assert not any(c.is_problem for c in custom.override_checks(cfg))


def test_a_copy_of_a_removed_built_in_file_is_an_orphan(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    place(cfg, "templates/il2ks/gone.html", with_header("<p>old</p>", "templates/il2ks/gone.html", 2))
    custom.copy_builtin(cfg, "css/site.css")
    (builtin["static"] / "css" / "site.css").unlink()
    assert states(cfg) == {SITE_CSS: "orphan", "templates/il2ks/gone.html": "orphan"}


def test_a_newer_version_than_the_built_in_one_is_flagged(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    place(cfg, HOME, with_header("x", HOME, 5))
    (check,) = custom.override_checks(cfg)
    assert check.state == "newer"
    assert "downgraded" in check.message


def test_files_without_a_version_use_the_copy_record_only(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    place(cfg, "static/vendor/lib.js", "var mine;")
    assert states(cfg) == {"static/vendor/lib.js": "unchecked"}  # never recorded, nothing to compare
    custom.copy_builtin(cfg, "static/vendor/lib.js", force=True)
    assert states(cfg) == {"static/vendor/lib.js": "current"}
    write_builtin(builtin["static"], "static/vendor/lib.js", "var lib2;\n", None)
    assert states(cfg) == {"static/vendor/lib.js": "outdated"}


def test_a_built_in_file_that_has_no_version_line_yet_is_not_judged(
    cfg: Config, builtin: dict[custom.Kind, Path]
) -> None:
    write_builtin(builtin["templates"], HOME, "<h1>new page, not bumped yet</h1>\n", None)
    place(cfg, HOME, "mine")
    assert states(cfg) == {HOME: "unchecked"}


def test_a_damaged_record_file_does_not_hide_the_version_check(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    place(cfg, HOME, with_header("x", HOME, 1))
    (cfg.data_dir / "custom" / ".il2ks-overrides.json").write_text("{not json", encoding="utf-8")
    upgrade_home(builtin)
    assert states(cfg) == {HOME: "outdated"}
    with pytest.raises(custom.CustomError, match="damaged"):
        custom.load_records(cfg)
    assert [f.level for f in serving_checks.custom_overrides(cfg)] == [Level.WARN, Level.WARN]


# --- accept ----------------------------------------------------------------------------------------------------------


def test_accept_sets_the_version_line_and_keeps_your_edits(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    placed = custom.copy_builtin(cfg, "il2ks/home.html")
    placed.override.write_text(placed.override.read_text(encoding="utf-8") + "<p>mine</p>\n", encoding="utf-8")
    upgrade_home(builtin, 2)
    assert states(cfg) == {HOME: "outdated"}
    custom.accept_original(cfg, "il2ks/home.html")
    text = placed.override.read_text(encoding="utf-8")
    assert text.startswith("{# il2ks-template: templates/il2ks/home.html v2 ")
    assert "home v1" in text
    assert "<p>mine</p>" in text
    assert states(cfg) == {HOME: "current"}


def test_accept_adds_a_missing_version_line(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    placed = place(cfg, HOME, "by hand\n")
    custom.accept_original(cfg, "il2ks/home.html")
    assert placed.read_text(encoding="utf-8").endswith("\nby hand\n")
    assert states(cfg) == {HOME: "current"}


def test_accept_needs_an_override_file(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    with pytest.raises(custom.CustomError, match="no override"):
        custom.accept_original(cfg, "il2ks/home.html")


# --- before the first release: every file is v1, the recorded hash tells ----------------------------------------------


def test_with_every_version_at_v1_a_changed_original_is_found_by_its_hash(
    cfg: Config, builtin: dict[custom.Kind, Path]
) -> None:
    placed = custom.copy_builtin(cfg, "il2ks/home.html")
    write_builtin(builtin["templates"], HOME, "<h1>home, changed</h1>\n", 1)  # new content, still v1
    (check,) = custom.override_checks(cfg)
    assert check.state == "outdated"
    assert (check.override_version, check.builtin_version) == (1, 1)
    assert check.is_problem
    assert "changed since you copied it" in check.message
    assert "il2ks custom diff templates/il2ks/home.html" in check.fix
    assert "home, changed" in custom.diff_override(cfg, "il2ks/home.html")
    custom.accept_original(cfg, "il2ks/home.html")  # refreshes the record
    assert states(cfg) == {HOME: "current"}
    assert placed.override.read_text(encoding="utf-8").startswith("{# il2ks-template: templates/il2ks/home.html v1 ")


def test_with_every_version_at_v1_an_untouched_original_stays_current(
    cfg: Config, builtin: dict[custom.Kind, Path]
) -> None:
    custom.copy_builtin(cfg, "il2ks/home.html")
    custom.copy_builtin(cfg, "css/site.css")
    assert states(cfg) == {HOME: "current", SITE_CSS: "current"}


# --- diff ------------------------------------------------------------------------------------------------------------


def test_diff_shows_your_file_against_the_built_in_one(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    placed = custom.copy_builtin(cfg, "il2ks/home.html")
    placed.override.write_text(
        placed.override.read_text(encoding="utf-8").replace("home v1", "my home"), encoding="utf-8"
    )
    upgrade_home(builtin, 2)
    out = custom.diff_override(cfg, "il2ks/home.html")
    assert "outdated" not in out  # the explanation is plain words, not the state name
    assert "version 1" in out
    assert "version 2" in out
    assert "-<h1>my home</h1>" in out
    assert "+<h1>home v2</h1>" in out
    assert "does not keep old versions" in out
    assert "il2ks custom accept templates/il2ks/home.html" in out


def test_diff_of_identical_files_says_so(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    custom.copy_builtin(cfg, "il2ks/home.html")
    assert "identical" in custom.diff_override(cfg, "il2ks/home.html")


def test_diff_needs_an_override(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    with pytest.raises(custom.CustomError, match="no override"):
        custom.diff_override(cfg, "il2ks/home.html")


# --- once per process, and the startup warning ------------------------------------------------------------------------


def test_the_startup_scan_is_computed_once(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    root = cfg.data_dir / "custom"
    place(cfg, HOME, with_header("x", HOME, 1))
    assert custom.startup_problems(root) == []
    upgrade_home(builtin)  # would be a problem now, but a running site doesn't look again
    assert custom.startup_problems(root) == []
    custom.startup_scan.cache_clear()
    assert [c.key for c in custom.startup_problems(root)] == [HOME]


def test_startup_logs_a_warning_per_problem(
    cfg: Config, builtin: dict[custom.Kind, Path], caplog: pytest.LogCaptureFixture
) -> None:
    place(cfg, HOME, "by hand")
    place(cfg, "static/my.png", "png")
    with caplog.at_level(logging.WARNING, logger="il2ks.test"):
        problems = custom.log_problems(cfg.data_dir / "custom", logging.getLogger("il2ks.test"))
    assert [c.key for c in problems] == [HOME]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2  # a summary and one line for the file
    assert HOME in warnings[1].getMessage()


def test_startup_logs_nothing_when_all_is_well(
    cfg: Config, builtin: dict[custom.Kind, Path], caplog: pytest.LogCaptureFixture
) -> None:
    custom.copy_builtin(cfg, "il2ks/home.html")
    with caplog.at_level(logging.WARNING, logger="il2ks.test"):
        assert custom.log_problems(cfg.data_dir / "custom", logging.getLogger("il2ks.test")) == []
    assert not caplog.records


def test_real_builtin_files_can_be_copied(cfg: Config) -> None:
    """The real lookup finds il2ks's own files and Django admin's (so admin branding can be overridden too)."""
    assert custom.builtin_file("static", "admin/css/base.css") is not None
    placed = custom.copy_builtin(cfg, "il2ks/base.html")
    assert placed.kind == "templates"
    assert templateversions.find_header(placed.override.read_text(encoding="utf-8"), "base.html") is not None
    assert states(cfg) == {"templates/il2ks/base.html": "current"}


# --- the doctor check -----------------------------------------------------------------------------------------------


def test_doctor_is_ok_for_a_current_copy_and_warns_after_an_upgrade(
    cfg: Config, builtin: dict[custom.Kind, Path]
) -> None:
    custom.copy_builtin(cfg, "il2ks/home.html")
    assert [f.level for f in serving_checks.custom_overrides(cfg)] == [Level.OK]
    upgrade_home(builtin)
    findings = list(serving_checks.custom_overrides(cfg))
    assert [f.level for f in findings] == [Level.WARN]
    assert "OUT OF DATE" in findings[0].title
    assert "il2ks custom diff templates/il2ks/home.html" in findings[0].fix
    assert "il2ks custom accept templates/il2ks/home.html" in findings[0].fix


def test_doctor_warns_per_file(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    place(cfg, HOME, "by hand")
    place(cfg, "templates/il2ks/gone.html", with_header("x", "templates/il2ks/gone.html", 1))
    place(cfg, SITE_CSS, with_header("x", SITE_CSS, 1))
    titles = [f.title for f in serving_checks.custom_overrides(cfg) if f.level is Level.WARN]
    assert len(titles) == 2
    assert any("no version line" in t for t in titles)
    assert any("no built-in original" in t for t in titles)


def test_doctor_is_silent_without_a_custom_folder(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    assert list(serving_checks.custom_overrides(cfg)) == []


def test_doctor_notes_only_new_files(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    place(cfg, "static/my-logo.png", "png")
    assert [f.level for f in serving_checks.custom_overrides(cfg)] == [Level.OK]


# --- through the CLI -------------------------------------------------------------------------------------------------


def test_cli_copy_list_diff_accept(
    cfg: Config,
    builtin: dict[custom.Kind, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("IL2KS_DATA_DIR", str(cfg.data_dir))
    monkeypatch.delenv("IL2KS_CONFIG", raising=False)
    assert main(["custom", "list"]) == EXIT_OK
    assert "no overrides yet" in capsys.readouterr().out

    assert main(["custom", "copy", "il2ks/home.html"]) == EXIT_OK
    assert "copied" in capsys.readouterr().out
    assert main(["custom", "copy", "il2ks/home.html"]) == EXIT_USAGE
    assert "--force" in capsys.readouterr().err

    assert main(["custom", "list"]) == EXIT_OK
    assert "up to date   templates/il2ks/home.html  (yours: v1, built-in: v1)" in capsys.readouterr().out

    upgrade_home(builtin, 2)
    assert main(["custom", "list"]) == EXIT_OK
    listing = capsys.readouterr().out
    assert "OUT OF DATE  templates/il2ks/home.html" in listing
    assert "version 1" in listing
    assert "version 2" in listing

    assert main(["custom", "diff", "templates/il2ks/home.html"]) == EXIT_OK
    assert "+<h1>home v2</h1>" in capsys.readouterr().out

    assert main(["custom", "accept", "templates/il2ks/home.html"]) == EXIT_OK
    assert main(["custom", "list"]) == EXIT_OK
    assert "up to date" in capsys.readouterr().out
    assert main(["custom", "diff", "static/css/site.css"]) == EXIT_USAGE
    assert "no override" in capsys.readouterr().err


def test_cli_list_builtin(builtin: dict[custom.Kind, Path], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["custom", "list", "--builtin"]) == EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert "templates/il2ks/home.html" in out
    assert "static/css/site.css" in out


def test_cli_list_problems_json_and_fail_on_problems(
    cfg: Config,
    builtin: dict[custom.Kind, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The Windows installer asks `custom list --problems --fail-on-problems` after an upgrade (TD-25, doc 07)."""
    monkeypatch.setenv("IL2KS_DATA_DIR", str(cfg.data_dir))
    monkeypatch.delenv("IL2KS_CONFIG", raising=False)
    # no overrides at all: nothing to report, exit status 0
    assert main(["custom", "list", "--problems", "--fail-on-problems"]) == EXIT_OK
    assert "no override needs attention" in capsys.readouterr().out
    assert main(["custom", "copy", "il2ks/home.html"]) == EXIT_OK
    capsys.readouterr()
    assert main(["custom", "list", "--problems", "--fail-on-problems"]) == EXIT_OK
    assert "no override needs attention" in capsys.readouterr().out

    upgrade_home(builtin, 2)
    assert main(["custom", "list", "--problems"]) == EXIT_OK  # without the flag the status stays 0
    assert "OUT OF DATE  templates/il2ks/home.html" in capsys.readouterr().out
    assert main(["custom", "list", "--problems", "--fail-on-problems"]) == EXIT_PROBLEMS
    capsys.readouterr()

    assert main(["custom", "list", "--json", "--problems", "--fail-on-problems"]) == EXIT_PROBLEMS
    document = json.loads(capsys.readouterr().out)
    assert document["problems"] == 1
    assert document["custom_dir"] == str(custom.custom_dir(cfg))
    [item] = document["overrides"]
    assert item["key"] == "templates/il2ks/home.html"
    assert item["state"] == "outdated"
    assert item["is_problem"] is True
    assert (item["override_version"], item["builtin_version"]) == (1, 2)
    assert "il2ks custom accept templates/il2ks/home.html" in item["fix"]

    # --json without --problems also lists the good ones
    assert main(["custom", "accept", "templates/il2ks/home.html"]) == EXIT_OK
    capsys.readouterr()
    assert main(["custom", "list", "--json", "--fail-on-problems"]) == EXIT_OK
    document = json.loads(capsys.readouterr().out)
    assert document["problems"] == 0
    assert [i["state"] for i in document["overrides"]] == ["current"]
