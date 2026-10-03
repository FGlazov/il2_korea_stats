"""`custom/` overrides (TD-25, FR-ADM-6): copy, list, accept, and the doctor check."""

import json
from pathlib import Path

import pytest

from il2ks.cli import EXIT_OK, EXIT_USAGE, main
from il2ks.config import Config, load_config
from il2ks.ops import serving_checks
from il2ks.ops.doctor import Level
from il2ks.serving import custom


@pytest.fixture
def builtin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[custom.Kind, Path]:
    """Two fake built-in roots, so tests don't depend on the real templates."""
    roots: dict[custom.Kind, Path] = {
        "templates": tmp_path / "pkg" / "templates",
        "static": tmp_path / "pkg" / "static",
    }
    (roots["templates"] / "il2ks").mkdir(parents=True)
    (roots["templates"] / "il2ks" / "home.html").write_text("<h1>home v1</h1>", encoding="utf-8")
    (roots["static"] / "css").mkdir(parents=True)
    (roots["static"] / "css" / "site.css").write_text("body {}", encoding="utf-8")
    (roots["templates"] / "both.txt").write_text("t", encoding="utf-8")
    (roots["static"] / "both.txt").write_text("s", encoding="utf-8")

    def fake_roots(kind: custom.Kind) -> list[Path]:
        return [roots[kind]]

    monkeypatch.setattr(custom, "builtin_roots", fake_roots)
    return roots


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return load_config(None, {"IL2KS_DATA_DIR": str(tmp_path / "data")}, create_server_uid=False)


def test_copy_a_template_by_bare_name(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    status = custom.copy_builtin(cfg, "il2ks/home.html")
    target = cfg.data_dir / "custom" / "templates" / "il2ks" / "home.html"
    assert status.override == target
    assert target.read_text(encoding="utf-8") == "<h1>home v1</h1>"
    record = json.loads((cfg.data_dir / "custom" / ".il2ks-overrides.json").read_text(encoding="utf-8"))
    entry = record["overrides"]["templates/il2ks/home.html"]
    assert entry["original_sha256"] == custom.sha256_of(builtin["templates"] / "il2ks" / "home.html")
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
    target = cfg.data_dir / "custom" / "templates" / "il2ks" / "home.html"
    target.write_text("my edit", encoding="utf-8")
    with pytest.raises(custom.CustomError, match="--force"):
        custom.copy_builtin(cfg, "il2ks/home.html")
    assert target.read_text(encoding="utf-8") == "my edit"
    custom.copy_builtin(cfg, "il2ks/home.html", force=True)
    assert target.read_text(encoding="utf-8") == "<h1>home v1</h1>"


@pytest.mark.parametrize("bad", ["../secret.txt", "/etc/passwd", "templates/../../x", "", "nope/missing.html"])
def test_unknown_or_unsafe_paths_are_refused(cfg: Config, builtin: dict[custom.Kind, Path], bad: str) -> None:
    with pytest.raises(custom.CustomError):
        custom.copy_builtin(cfg, bad)
    assert not (cfg.data_dir / "custom").exists()


def test_status_follows_the_original(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    custom.copy_builtin(cfg, "il2ks/home.html")
    custom.copy_builtin(cfg, "css/site.css")
    assert {s.key: s.state for s in custom.override_statuses(cfg)} == {
        "templates/il2ks/home.html": "ok",
        "static/css/site.css": "ok",
    }

    (builtin["templates"] / "il2ks" / "home.html").write_text("<h1>home v2</h1>", encoding="utf-8")  # an upgrade
    (builtin["static"] / "css" / "site.css").unlink()  # an upgrade removed the file
    states = {s.key: s.state for s in custom.override_statuses(cfg)}
    assert states == {
        "templates/il2ks/home.html": "original-changed",
        "static/css/site.css": "original-missing",
    }


def test_your_own_edits_do_not_count_as_a_changed_original(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    custom.copy_builtin(cfg, "il2ks/home.html")
    (cfg.data_dir / "custom" / "templates" / "il2ks" / "home.html").write_text("edited", encoding="utf-8")
    assert [s.state for s in custom.override_statuses(cfg)] == ["ok"]


def test_accept_records_the_new_original(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    custom.copy_builtin(cfg, "il2ks/home.html")
    (builtin["templates"] / "il2ks" / "home.html").write_text("<h1>home v2</h1>", encoding="utf-8")
    assert custom.override_statuses(cfg)[0].state == "original-changed"
    custom.accept_original(cfg, "il2ks/home.html")
    assert custom.override_statuses(cfg)[0].state == "ok"


def test_accept_needs_a_recorded_override(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    with pytest.raises(custom.CustomError, match="not a recorded override"):
        custom.accept_original(cfg, "il2ks/home.html")


def test_a_deleted_override_is_reported_not_warned(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    custom.copy_builtin(cfg, "il2ks/home.html")
    (cfg.data_dir / "custom" / "templates" / "il2ks" / "home.html").unlink()
    assert custom.override_statuses(cfg)[0].state == "override-deleted"
    assert [f.level for f in serving_checks.custom_overrides(cfg)] == []


def test_hand_placed_overrides_are_found_but_new_files_are_not(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    base = cfg.data_dir / "custom"
    (base / "templates" / "il2ks").mkdir(parents=True)
    (base / "templates" / "il2ks" / "home.html").write_text("by hand", encoding="utf-8")
    (base / "static").mkdir(parents=True)
    (base / "static" / "my-logo.png").write_bytes(b"png")  # no built-in counterpart: just a new file
    found = custom.untracked_overrides(cfg)
    assert [(k, r) for k, r, _ in found] == [("templates", "il2ks/home.html")]


def test_a_damaged_record_file_is_explained(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    (cfg.data_dir / "custom").mkdir(parents=True)
    (cfg.data_dir / "custom" / ".il2ks-overrides.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(custom.CustomError, match="damaged"):
        custom.override_statuses(cfg)
    findings = list(serving_checks.custom_overrides(cfg))
    assert [f.level for f in findings] == [Level.WARN]


def test_real_builtin_files_can_be_copied(cfg: Config) -> None:
    """The real lookup finds Django admin's static files and templates (so admin branding can be overridden too)."""
    assert custom.builtin_file("static", "admin/css/base.css") is not None
    status = custom.copy_builtin(cfg, "admin/base.html")
    assert status.kind == "templates"
    assert status.override.is_file()


# --- the doctor check -----------------------------------------------------------------------------------------------


def test_doctor_warns_when_an_original_changed_since_the_copy(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    custom.copy_builtin(cfg, "il2ks/home.html")
    assert [f.level for f in serving_checks.custom_overrides(cfg)] == [Level.OK]
    (builtin["templates"] / "il2ks" / "home.html").write_text("<h1>home v2</h1>", encoding="utf-8")
    findings = list(serving_checks.custom_overrides(cfg))
    assert [f.level for f in findings] == [Level.WARN]
    assert "may be out of date" in findings[0].title
    assert "il2ks custom accept templates/il2ks/home.html" in findings[0].fix


def test_doctor_warns_when_the_original_is_gone(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    custom.copy_builtin(cfg, "css/site.css")
    (builtin["static"] / "css" / "site.css").unlink()
    findings = list(serving_checks.custom_overrides(cfg))
    assert [f.level for f in findings] == [Level.WARN]
    assert "no built-in original" in findings[0].title


def test_doctor_is_silent_without_a_custom_folder(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    assert list(serving_checks.custom_overrides(cfg)) == []


def test_doctor_warns_about_hand_placed_overrides(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    path = cfg.data_dir / "custom" / "templates" / "il2ks" / "home.html"
    path.parent.mkdir(parents=True)
    path.write_text("by hand", encoding="utf-8")
    findings = list(serving_checks.custom_overrides(cfg))
    assert [f.level for f in findings] == [Level.WARN]
    assert "not recorded" in findings[0].title


def test_doctor_notes_only_new_files(cfg: Config, builtin: dict[custom.Kind, Path]) -> None:
    path = cfg.data_dir / "custom" / "static" / "my-logo.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"png")
    assert [f.level for f in serving_checks.custom_overrides(cfg)] == [Level.OK]


# --- through the CLI -------------------------------------------------------------------------------------------------


def test_cli_copy_list_accept(
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
    assert "templates/il2ks/home.html: up to date" in capsys.readouterr().out

    (builtin["templates"] / "il2ks" / "home.html").write_text("changed", encoding="utf-8")
    assert main(["custom", "list"]) == EXIT_OK
    listing = capsys.readouterr().out
    assert "ORIGINAL CHANGED" in listing
    assert "built-in:" in listing

    assert main(["custom", "accept", "templates/il2ks/home.html"]) == EXIT_OK
    assert main(["custom", "list"]) == EXIT_OK
    assert "up to date" in capsys.readouterr().out


def test_cli_list_builtin(builtin: dict[custom.Kind, Path], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["custom", "list", "--builtin"]) == EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert "templates/il2ks/home.html" in out
    assert "static/css/site.css" in out
