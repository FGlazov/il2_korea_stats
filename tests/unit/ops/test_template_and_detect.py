"""Filling the example `il2ks.toml` (FR-OPS-1) and finding DServer's log folder (bounded search)."""

from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from il2ks.config import BackupConfig, load_config
from il2ks.ops.detect import default_roots, find_log_folders, inspect_folder
from il2ks.ops.template import TemplateError, fill_template, template_text, toml_string

UID = "11111111-2222-3333-4444-555555555555"


def comment_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("# ")]


def test_filling_keeps_every_explanation_and_only_switches_on_what_was_chosen() -> None:
    text = template_text()
    filled = fill_template(text, {("", "data_dir"): '"/srv/il2ks"', ("server", "timezone"): '"Asia/Seoul"'})
    assert comment_lines(filled) == comment_lines(text)
    assert 'data_dir = "/srv/il2ks"' in filled
    assert "\n[server]\n" in filled
    assert 'timezone = "Asia/Seoul"' in filled
    assert "#data_dir" not in filled
    assert filled.count("#timezone") == 1  # [server]'s is switched on; [tours]' own timezone stays commented out
    assert "#[server]" not in filled
    # Tables the admin set nothing in stay commented out, with their defaults visible.
    assert "#[logs]" in filled
    assert "#after_archive" in filled
    raw = tomllib.loads(filled)
    assert raw == {"data_dir": "/srv/il2ks", "server": {"timezone": "Asia/Seoul"}}


def test_a_filled_template_loads_as_a_config(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    values = {
        ("", "data_dir"): toml_string(tmp_path / "data"),
        ("logs", "dir"): toml_string(logs),
        ("server", "timezone"): toml_string("Europe/Berlin"),
        ("server", "uid"): toml_string(UID),
        ("backup", "keep"): "4",
    }
    file = tmp_path / "il2ks.toml"
    file.write_text(fill_template(template_text(), values), encoding="utf-8")
    cfg = load_config(file, {})
    assert (cfg.data_dir, cfg.logs.dir, cfg.timezone_name, str(cfg.server_uid)) == (
        tmp_path / "data",
        logs,
        "Europe/Berlin",
        UID,
    )
    assert cfg.backup == BackupConfig(keep=4, daily=True)


@pytest.mark.parametrize("path", ['C:\\Users\\Hans\\IL-2 "Korea"\\logs', "/home/o'brien/\u00e9t\u00e9", "plain"])
def test_strings_survive_quotes_backslashes_and_non_ascii(path: str) -> None:
    filled = fill_template(template_text(), {("logs", "dir"): toml_string(path)})
    assert tomllib.loads(filled)["logs"]["dir"] == path


def test_a_key_the_template_lacks_in_a_table_it_has_is_an_error() -> None:
    with pytest.raises(TemplateError, match=r"\[logs\] nonsense"):
        fill_template(template_text(), {("logs", "nonsense"): "1"})
    with pytest.raises(TemplateError, match="top_level_nonsense"):
        fill_template(template_text(), {("", "top_level_nonsense"): "1"})


def test_a_table_the_template_does_not_have_is_appended() -> None:
    filled = fill_template(
        template_text(), {("zzz_new", "mode"): '"x"', ("zzz_new", "domain"): '"d"', ("server", "uid"): f'"{UID}"'}
    )
    raw = tomllib.loads(filled)
    assert raw["zzz_new"] == {"mode": "x", "domain": "d"}
    assert raw["server"] == {"uid": UID}


def test_the_example_file_has_no_stray_uncommented_lines_after_filling_nothing() -> None:
    assert fill_template(template_text(), {}).splitlines() == template_text().splitlines()


# --- finding the log folder -----------------------------------------------------------------------------------------


def make_reports(folder: Path, count: int = 2, *, mtime: float | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        path = folder / f"missionReport(2026-09-19_10-00-00)[{i}].txt"
        path.write_text("T:0 AType:15\n", encoding="utf-8")
        if mtime is not None:
            os.utime(path, (mtime, mtime))


def test_a_folder_counts_when_it_holds_mission_report_text_files(tmp_path: Path) -> None:
    make_reports(tmp_path / "logs", 3)
    (tmp_path / "logs" / "notes.txt").write_text("x", encoding="utf-8")
    hit = inspect_folder(tmp_path / "logs")
    assert hit is not None
    assert hit.reports == 3
    assert inspect_folder(tmp_path) is None
    assert inspect_folder(tmp_path / "missing") is None


def test_the_search_finds_nested_logs_in_a_game_install_and_ranks_the_newest_first(tmp_path: Path) -> None:
    root = tmp_path / "Program Files"
    make_reports(root / "IL-2 Sturmovik Great Battles" / "data" / "logs" / "txt", 2, mtime=2_000_000_000)
    make_reports(root / "IL-2 Korea Server" / "dserver" / "logs", 5, mtime=1_900_000_000)
    make_reports(root / "Some Other Tool" / "logs", 1, mtime=2_100_000_000)  # not game-looking: never visited
    found = find_log_folders([root])
    assert [f.path.name for f in found] == ["txt", "logs"]
    assert [f.reports for f in found] == [2, 5]


def test_the_search_stops_at_its_entry_budget(tmp_path: Path) -> None:
    root = tmp_path / "games"
    for i in range(30):
        (root / f"folder{i}" / "sub").mkdir(parents=True)
    make_reports(root / "zzz_il2" / "logs")
    assert find_log_folders([root], max_entries=5) == []
    assert find_log_folders([root], max_entries=10_000) != []


def test_the_search_stops_at_its_time_budget(tmp_path: Path) -> None:
    root = tmp_path / "games"
    make_reports(root / "il2" / "logs")
    ticks = iter(range(1000))
    assert find_log_folders([root], seconds=2.0, clock=lambda: float(next(ticks)) * 10) == []


def test_the_search_stops_at_its_depth_limit(tmp_path: Path) -> None:
    deep = tmp_path / "games" / "il2" / "b" / "c" / "d"
    make_reports(deep)
    assert find_log_folders([tmp_path / "games"], max_depth=2) == []
    assert len(find_log_folders([tmp_path / "games"], max_depth=6)) == 1


def test_roots_for_windows_are_the_usual_install_places(tmp_path: Path) -> None:
    (tmp_path / "PF").mkdir()
    (tmp_path / "PF86" / "Steam" / "steamapps" / "common").mkdir(parents=True)
    env = {"ProgramFiles": str(tmp_path / "PF"), "ProgramFiles(x86)": str(tmp_path / "PF86")}
    roots = list(default_roots("win32", env, tmp_path / "home", fixed_drive=lambda letter: False))
    assert tmp_path / "PF" in roots
    assert tmp_path / "PF86" / "Steam" / "steamapps" / "common" in roots
    assert all(r.is_dir() for r in roots)


def test_roots_for_wine_start_at_the_prefix(tmp_path: Path) -> None:
    prefix = tmp_path / "wineprefix"
    (prefix / "drive_c" / "Program Files").mkdir(parents=True)
    (tmp_path / "home" / ".wine" / "drive_c" / "Games").mkdir(parents=True)
    roots = list(default_roots("linux", {"WINEPREFIX": str(prefix)}, tmp_path / "home"))
    assert prefix / "drive_c" / "Program Files" in roots
    assert tmp_path / "home" / ".wine" / "drive_c" / "Games" in roots
    assert roots.index(prefix / "drive_c" / "Program Files") < roots.index(tmp_path / "home")


@pytest.mark.skipif(sys.platform != "win32", reason="drive letters only parse as drives on Windows")
def test_only_fixed_drives_are_probed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A disconnected network drive can stall a probe for many seconds: such letters are never touched."""
    from il2ks.ops import detect

    probed: list[Path] = []

    def spy(path: Path) -> bool:
        probed.append(path)
        return False

    monkeypatch.setattr(detect, "_is_dir", spy)
    list(default_roots("win32", {}, tmp_path / "home", fixed_drive=lambda letter: letter == "E"))
    drives = {p.drive for p in probed if p.drive}
    assert "E:" in drives
    assert not drives & {"D:", "F:", "G:"}


def test_the_clock_runs_while_the_roots_are_being_probed(tmp_path: Path) -> None:
    root = tmp_path / "games"
    make_reports(root / "il2" / "logs")
    now = [0.0]

    def slow_roots() -> Iterator[Path]:
        now[0] += 100.0  # probing this root took 100 s
        yield root

    assert find_log_folders(slow_roots(), seconds=5.0, clock=lambda: now[0]) == []


def test_a_folder_with_endless_files_is_judged_on_what_the_budget_allowed(tmp_path: Path) -> None:
    folder = tmp_path / "logs"
    make_reports(folder, 50)
    hit = inspect_folder(folder, spent=iter([False] * 10 + [True] * 100).__next__)
    assert hit is not None
    assert hit.reports <= 10
