"""Every doctor check of the ops area, OK / WARN / ERROR (FR-OPS-1), the report and the command's exit codes."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import pytest
from django.db.migrations.loader import MigrationLoader

from il2ks.cli import main
from il2ks.config import BackupConfig, Config, LogsConfig
from il2ks.db.models import IngestRun, IngestStatus
from il2ks.ops import backup, checks
from il2ks.ops.doctor import Check, Finding, Level, run_checks
from il2ks.ops.report import exit_code, render_json, render_text, use_color
from tests.ops_helpers import make_instance, returning, write_db

NOW = datetime.now(UTC)
DAY = 86400.0


def only(check_fn: Check, cfg: Config) -> Finding:
    findings = list(check_fn(cfg))
    assert len(findings) == 1, findings
    return findings[0]


def levels(findings: Iterable[Finding]) -> list[Level]:
    return [f.level for f in findings]


def all_migrations() -> list[tuple[str, str]]:
    return sorted(MigrationLoader(None, ignore_no_migrations=True).graph.leaf_nodes())


# --- data folder ----------------------------------------------------------------------------------------------------


def test_data_dir_ok(tmp_path: Path) -> None:
    assert only(checks.data_dir_check, make_instance(tmp_path)).level is Level.OK


def test_data_dir_missing_is_an_error_with_a_fix(tmp_path: Path) -> None:
    cfg = replace(make_instance(tmp_path), data_dir=tmp_path / "nope")
    finding = only(checks.data_dir_check, cfg)
    assert finding.level is Level.ERROR
    assert "il2ks setup" in finding.fix


def test_data_dir_not_writable_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> object:
        raise PermissionError("denied")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", refuse)
    finding = only(checks.data_dir_check, make_instance(tmp_path))
    assert finding.level is Level.ERROR
    assert "not writable" in finding.title


# --- database -------------------------------------------------------------------------------------------------------


def test_database_missing_is_an_error(tmp_path: Path) -> None:
    finding = only(checks.database_check, make_instance(tmp_path, with_db=False))
    assert finding.level is Level.ERROR
    assert "il2ks setup" in finding.fix


def test_database_that_is_not_a_database_is_an_error(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path, with_db=False)
    cfg.db_path.write_bytes(b"garbage" * 100)
    finding = only(checks.database_check, cfg)
    assert finding.level is Level.ERROR
    assert "il2ks restore" in finding.fix


def test_database_with_pending_migrations_is_a_warning(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path, with_db=False)
    write_db(cfg.db_path, migrations=all_migrations()[:-1])
    finding = only(checks.database_check, cfg)
    assert finding.level is Level.WARN
    assert "1 pending" in finding.title
    assert "automatic backup" in finding.fix


def test_database_up_to_date_is_ok(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path, with_db=False)
    write_db(cfg.db_path, migrations=all_migrations())
    assert only(checks.database_check, cfg).level is Level.OK


def test_database_check_stays_quiet_when_the_data_folder_is_missing(tmp_path: Path) -> None:
    cfg = replace(make_instance(tmp_path), data_dir=tmp_path / "nope")
    assert list(checks.database_check(cfg)) == []
    assert not (tmp_path / "nope").exists()  # looking never creates anything


# --- log folder -----------------------------------------------------------------------------------------------------


def with_logs(cfg: Config, folder: Path | None) -> Config:
    return replace(cfg, logs=LogsConfig(dir=folder))


def report_file(folder: Path, age_days: float, name: str = "missionReport(2026-09-19_10-00-00)[0].txt") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("x", encoding="utf-8")
    when = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (when, when))


def test_log_folder_not_configured_is_a_warning(tmp_path: Path) -> None:
    finding = only(checks.log_folder_check, with_logs(make_instance(tmp_path), None))
    assert finding.level is Level.WARN
    assert "--from" in finding.fix


def test_log_folder_that_does_not_exist_is_an_error(tmp_path: Path) -> None:
    finding = only(checks.log_folder_check, with_logs(make_instance(tmp_path), tmp_path / "gone"))
    assert finding.level is Level.ERROR
    assert "Wine" in finding.fix


def test_no_reports_at_all_explains_the_text_log_setting_and_admits_the_key_is_unknown(tmp_path: Path) -> None:
    (tmp_path / "logs").mkdir()
    finding = only(checks.log_folder_check, with_logs(make_instance(tmp_path), tmp_path / "logs"))
    assert finding.level is Level.WARN
    assert "text logs" in finding.title
    assert "startup.cfg" in finding.fix
    assert "not confirmed" in finding.fix  # OQ-1: the Korea key is unknown


def test_recent_reports_are_ok(tmp_path: Path) -> None:
    report_file(tmp_path / "logs", 0.1)
    finding = only(checks.log_folder_check, with_logs(make_instance(tmp_path), tmp_path / "logs"))
    assert finding.level is Level.OK
    assert "1 mission report" in finding.detail


def test_old_reports_are_a_warning(tmp_path: Path) -> None:
    report_file(tmp_path / "logs", 30)
    finding = only(checks.log_folder_check, with_logs(make_instance(tmp_path), tmp_path / "logs"))
    assert finding.level is Level.WARN
    assert "30 days old" in finding.title


def test_an_empty_folder_is_fine_when_ingested_files_move_out(tmp_path: Path) -> None:
    """Ingestion moves the originals away (FR-ING-10), so an empty log folder next to a fresh archive is normal."""
    cfg = with_logs(make_instance(tmp_path), tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    month = cfg.archive_dir / "2026" / "09"
    month.mkdir(parents=True)
    (month / "missionReport(2026-09-19_10-00-00)[0].txt.zip").write_bytes(b"z")
    finding = only(checks.log_folder_check, cfg)
    assert finding.level is Level.OK
    assert "empty" in finding.detail


def test_other_files_in_the_log_folder_do_not_count_as_reports(tmp_path: Path) -> None:
    report_file(tmp_path / "logs", 0.1, name="readme.txt")
    finding = only(checks.log_folder_check, with_logs(make_instance(tmp_path), tmp_path / "logs"))
    assert finding.level is Level.WARN


# --- timezone, server ID, disk --------------------------------------------------------------------------------------


def test_windows_without_a_configured_timezone_is_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checks, "_is_windows", returning(True))
    cfg = make_instance(tmp_path)
    assert cfg.source is not None
    cfg.source.write_text('data_dir = "x"\n', encoding="utf-8")  # no [server] timezone
    monkeypatch.delenv("IL2KS_SERVER_TIMEZONE", raising=False)
    finding = only(checks.timezone_check, cfg)
    assert finding.level is Level.WARN
    assert "[server] timezone" in finding.fix


def test_windows_with_a_configured_timezone_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checks, "_is_windows", returning(True))
    assert only(checks.timezone_check, make_instance(tmp_path)).level is Level.OK  # the fixture sets "UTC"


def test_windows_timezone_from_the_environment_counts_as_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(checks, "_is_windows", returning(True))
    cfg = make_instance(tmp_path)
    assert cfg.source is not None
    cfg.source.write_text('data_dir = "x"\n', encoding="utf-8")
    monkeypatch.setenv("IL2KS_SERVER_TIMEZONE", "Asia/Seoul")
    assert only(checks.timezone_check, cfg).level is Level.OK


def test_other_systems_detect_the_timezone_and_are_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checks, "_is_windows", returning(False))
    cfg = make_instance(tmp_path)
    assert cfg.source is not None
    cfg.source.write_text('data_dir = "x"\n', encoding="utf-8")
    assert only(checks.timezone_check, cfg).level is Level.OK


def test_server_uid_present_in_the_config_or_the_file_is_ok(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    assert only(checks.server_uid_check, cfg).level is Level.OK
    assert cfg.source is not None
    cfg.source.write_text('data_dir = "x"\n', encoding="utf-8")
    assert only(checks.server_uid_check, cfg).level is Level.OK  # server_uid.txt is still there
    (cfg.data_dir / "server_uid.txt").unlink()
    finding = only(checks.server_uid_check, cfg)
    assert finding.level is Level.WARN
    assert "il2ks setup" in finding.fix


class Usage(NamedTuple):
    total: int
    used: int
    free: int


@pytest.mark.parametrize(
    ("free_bytes", "level"),
    [(50 << 30, Level.OK), (500 << 20, Level.WARN), (50 << 20, Level.ERROR)],
)
def test_disk_space_levels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, free_bytes: int, level: Level) -> None:
    monkeypatch.setattr(shutil, "disk_usage", returning(Usage(1 << 40, 0, free_bytes)))
    assert only(checks.disk_space_check, make_instance(tmp_path)).level is level


def test_disk_space_of_a_data_folder_that_does_not_exist_yet_looks_at_its_parent(tmp_path: Path) -> None:
    cfg = replace(make_instance(tmp_path), data_dir=tmp_path / "a" / "b")
    assert only(checks.disk_space_check, cfg).level in {Level.OK, Level.WARN, Level.ERROR}


# --- ingestion health -----------------------------------------------------------------------------------------------


def run(
    uid: str,
    status: IngestStatus,
    *,
    hours_ago: float,
    retry: bool = False,
    atypes: dict[str, int] | None = None,
    keys: dict[str, int] | None = None,
) -> IngestRun:
    started = NOW - timedelta(hours=hours_ago)
    return IngestRun.objects.create(
        mission_uid=uid,
        status=status,
        fingerprint="f",
        started_at=started,
        next_retry_at=started + timedelta(minutes=5) if retry else None,
        unknown_atypes=atypes or {},
        unknown_keys=keys or {},
    )


@pytest.fixture
def ingest_cfg(tmp_path: Path) -> Config:
    return make_instance(tmp_path)  # a database file exists, so the check looks at the (test) database


@pytest.mark.django_db
def test_ingestion_without_problems_is_ok(ingest_cfg: Config) -> None:
    run("2026-09-19_10-00-00", IngestStatus.OK, hours_ago=3)
    assert levels(checks.ingestion_check(ingest_cfg)) == [Level.OK]


@pytest.mark.django_db
def test_a_failed_mission_that_will_be_retried_is_a_warning(ingest_cfg: Config) -> None:
    run("2026-09-19_10-00-00", IngestStatus.FAILED, hours_ago=1, retry=True)
    findings = list(checks.ingestion_check(ingest_cfg))
    assert levels(findings) == [Level.WARN]
    assert "will be retried" in findings[0].title
    assert "2026-09-19_10-00-00" in findings[0].detail


@pytest.mark.django_db
def test_a_mission_il2ks_gave_up_on_is_an_error(ingest_cfg: Config) -> None:
    run("2026-09-19_10-00-00", IngestStatus.FAILED, hours_ago=5, retry=True)
    run("2026-09-19_10-00-00", IngestStatus.FAILED, hours_ago=1, retry=False)  # the newest failure has no retry left
    findings = list(checks.ingestion_check(ingest_cfg))
    assert levels(findings) == [Level.ERROR]
    assert "gave up" in findings[0].title
    assert "reprocess" in findings[0].fix


@pytest.mark.django_db
def test_a_mission_that_failed_and_later_succeeded_is_not_reported(ingest_cfg: Config) -> None:
    run("2026-09-19_10-00-00", IngestStatus.FAILED, hours_ago=5)
    run("2026-09-19_10-00-00", IngestStatus.OK, hours_ago=1)
    assert levels(checks.ingestion_check(ingest_cfg)) == [Level.OK]


@pytest.mark.django_db
def test_unknown_event_types_are_a_warning(ingest_cfg: Config) -> None:
    run("2026-09-19_10-00-00", IngestStatus.OK, hours_ago=2, atypes={"99": 3}, keys={"12:FOO": 1})
    findings = list(checks.ingestion_check(ingest_cfg))
    assert levels(findings) == [Level.OK, Level.WARN]
    assert "AType 99 (3x)" in findings[1].detail
    assert "key 12:FOO (1x)" in findings[1].detail


@pytest.mark.django_db
def test_ingestion_check_does_nothing_when_there_is_no_database_file(tmp_path: Path) -> None:
    assert list(checks.ingestion_check(make_instance(tmp_path, with_db=False))) == []


# --- backups --------------------------------------------------------------------------------------------------------


def test_no_backup_yet_is_a_warning(tmp_path: Path) -> None:
    finding = only(checks.backup_check, make_instance(tmp_path))
    assert finding.level is Level.WARN
    assert "il2ks backup" in finding.fix


def test_a_recent_backup_is_ok(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    backup.create_backup(cfg)
    assert only(checks.backup_check, cfg).level is Level.OK


def test_an_old_backup_is_a_warning_that_blames_the_missing_watcher(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    backup.create_backup(cfg, now=lambda: NOW - timedelta(days=10))
    finding = only(checks.backup_check, cfg)
    assert finding.level is Level.WARN
    assert "10 days old" in finding.title
    assert "il2ks watch" in finding.fix


def test_with_daily_backups_off_a_week_old_backup_is_still_fine(tmp_path: Path) -> None:
    cfg = replace(make_instance(tmp_path), backup=BackupConfig(daily=False))
    backup.create_backup(cfg, now=lambda: NOW - timedelta(days=7))
    assert only(checks.backup_check, cfg).level is Level.OK


# --- registry, report, command --------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_run_checks_runs_the_ops_checks(tmp_path: Path) -> None:
    findings = run_checks(make_instance(tmp_path))
    titles = " | ".join(f.title for f in findings)
    for expected in ("Data folder", "Database", "log folder", "timezone", "Server ID", "disk space", "backup"):
        assert expected.lower() in titles.lower(), expected


def test_exit_code_is_the_worst_level() -> None:
    ok, warn, error = Finding(Level.OK, "a"), Finding(Level.WARN, "b"), Finding(Level.ERROR, "c")
    assert exit_code([]) == 0
    assert exit_code([ok]) == 0
    assert exit_code([ok, warn]) == 1
    assert exit_code([warn, error, ok]) == 2


def test_text_report_groups_errors_first_and_shows_what_to_do() -> None:
    findings = [
        Finding(Level.OK, "Fine thing", "all good"),
        Finding(Level.ERROR, "Broken thing", "it broke", "Fix it like this"),
        Finding(Level.WARN, "Odd thing", fix="Look at it"),
    ]
    text = render_text(findings, color=False)
    assert text.index("ERROR (1)") < text.index("WARN (1)") < text.index("OK (1)")
    assert "[ERROR] Broken thing" in text
    assert "What to do: Fix it like this" in text
    assert "1 error(s), 1 warning(s)" in text
    assert "\x1b[" not in text


def test_coloured_report_uses_ansi_codes() -> None:
    text = render_text([Finding(Level.ERROR, "Broken")], color=True)
    assert "\x1b[31m" in text


class FakeStream:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.mark.parametrize(
    ("tty", "env", "expected"),
    [(False, {}, False), (True, {"NO_COLOR": "1"}, False), (True, {"TERM": "dumb"}, False)],
)
def test_colour_is_off_without_a_terminal_or_when_asked(tty: bool, env: dict[str, str], expected: bool) -> None:
    assert use_color(FakeStream(tty), env) is expected  # type: ignore[arg-type]  # only isatty() is used


def test_json_report_is_machine_readable() -> None:
    data = json.loads(render_json([Finding(Level.WARN, "t", "d", "f")]))
    assert data == {"exit_code": 1, "findings": [{"level": "WARN", "title": "t", "detail": "d", "fix": "f"}]}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[[Config], None]:
    for name in ("IL2KS_CONFIG", "IL2KS_LOGS_DIR", "IL2KS_SERVER_TIMEZONE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IL2KS_DATA_DIR", str(tmp_path / "data"))

    def point_at(cfg: Config) -> None:
        monkeypatch.setenv("IL2KS_DATA_DIR", str(cfg.data_dir))

    return point_at


@pytest.mark.django_db
def test_doctor_command_json_and_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], clean_env: Callable[[Config], None]
) -> None:
    cfg = make_instance(tmp_path)
    clean_env(cfg)
    code = main(["--config", str(cfg.source), "doctor", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == data["exit_code"] == max(Level[f["level"]] for f in data["findings"])
    assert data["findings"][0]["title"] == "Configuration file found and valid"
    assert {f["level"] for f in data["findings"]} <= {"OK", "WARN", "ERROR"}


def test_doctor_with_an_invalid_config_is_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], clean_env: Callable[[Config], None]
) -> None:
    bad = tmp_path / "il2ks.toml"
    bad.write_text("log_level = [oops\n", encoding="utf-8")
    assert main(["--config", str(bad), "doctor"]) == 2
    out = capsys.readouterr().out
    assert "Configuration file is invalid" in out
    assert "ERROR" in out


@pytest.mark.django_db
def test_doctor_without_any_config_warns_and_creates_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], clean_env: Callable[[Config], None]
) -> None:
    (tmp_path / "data").mkdir()
    code = main(["doctor"])
    out = capsys.readouterr().out
    assert code == 2  # no database yet
    assert "No il2ks.toml found" in out
    assert list((tmp_path / "data").iterdir()) == []  # no server_uid.txt, no database file: doctor only looks


def test_check_modules_include_the_ops_checks() -> None:
    from il2ks.ops.doctor import CHECK_MODULES

    assert "il2ks.ops.checks" in CHECK_MODULES
