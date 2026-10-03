"""CLI smoke tests: exit codes and messages of the job commands (FR-OPS-1, FR-ING-20)."""

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from il2ks import logsetup
from il2ks.cli import EXIT_FAILED, EXIT_LOCKED, EXIT_OK, EXIT_USAGE, PLANNED, main
from il2ks.config import Config
from il2ks.ingest import reprocess as reprocess_mod
from il2ks.ingest import runner
from il2ks.ingest import watch as watch_mod
from il2ks.ingest.lock import WriterLock
from tests.ingest_fakes import FakeSteps, make_pipeline, write_parts, write_zip

A, B = "2026-09-19_10-00-00", "2026-09-19_13-00-00"
LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, FakeSteps]:
    """Env-configured data and log dirs, fake pipeline, no log files written outside tmp."""
    data, logs = tmp_path / "data", tmp_path / "logs"
    logs.mkdir()
    for name in ("IL2KS_CONFIG", "IL2KS_LOGS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IL2KS_DATA_DIR", str(data))
    monkeypatch.setenv("IL2KS_LOGS_DIR", str(logs))
    steps = FakeSteps()

    def no_logging(
        process: str, log_dir: Path, level: str = "INFO", *, server_uid: uuid.UUID | None = None, keep_days: int = 14
    ) -> None:
        return None

    def fake_pipeline(cfg: Config, *, defer_ratings: bool = False) -> runner.Pipeline:
        return make_pipeline(steps)

    monkeypatch.setattr(logsetup, "configure_logging", no_logging)
    monkeypatch.setattr(runner, "default_pipeline", fake_pipeline)
    return data, logs, steps


def test_the_planned_commands_are_the_ones_fr_ops_1_and_fr_ops_6_list() -> None:
    assert set(PLANNED) == {"setup", "createadmin", "doctor", "backup", "restore"}


@pytest.mark.parametrize("command", sorted(PLANNED))
def test_planned_commands_are_stubs(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([command]) == EXIT_USAGE
    out = capsys.readouterr()
    assert out.err.strip() == f"il2ks {command}: not implemented yet (planned: {PLANNED[command][0]})"
    assert out.out == ""


def test_stub_help_names_the_requirement(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["createadmin", "--help"])
    assert info.value.code == 0
    assert "planned: FR-OPS-1" in capsys.readouterr().out


def test_every_job_command_has_help() -> None:
    for command in ("ingest", "watch", "reprocess", "rebuild-aggregates"):
        with pytest.raises(SystemExit) as info:
            main([command, "--help"])
        assert info.value.code == 0


def test_manage_still_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IL2KS_DATA_DIR", str(tmp_path))
    assert main(["manage", "check"]) == EXIT_OK


@pytest.mark.django_db
def test_ingest_processes_complete_missions_and_exits_zero(
    setup: tuple[Path, Path, FakeSteps], capsys: pytest.CaptureFixture[str]
) -> None:
    data, logs, steps = setup
    write_parts(logs, A, ["a"], age_s=0, now=LONG_AGO, end=False)  # complete by the idle timeout
    assert main(["ingest"]) == EXIT_OK
    assert "ingested 1" in capsys.readouterr().out
    assert [uid for uid, _ in steps.parsed] == [A]
    assert (data / "archive" / "2026" / "09").is_dir()
    assert not any(logs.iterdir())  # moved out of the log folder (FR-ING-10)


@pytest.mark.django_db
def test_logging_gets_the_server_uid_and_keep_days(
    setup: tuple[Path, Path, FakeSteps], monkeypatch: pytest.MonkeyPatch
) -> None:
    """TD-27: every JSON log line carries the server UID; keep_days comes from `log_keep_days`."""
    uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    monkeypatch.setenv("IL2KS_SERVER_UID", uid)
    monkeypatch.setenv("IL2KS_LOG_KEEP_DAYS", "3")
    calls: list[tuple[str, Path, str, uuid.UUID | None, int]] = []

    def record(
        process: str, log_dir: Path, level: str = "INFO", *, server_uid: uuid.UUID | None = None, keep_days: int = 14
    ) -> None:
        calls.append((process, log_dir, level, server_uid, keep_days))

    monkeypatch.setattr(logsetup, "configure_logging", record)
    assert main(["ingest"]) == EXIT_OK
    data, _, _ = setup
    assert calls == [("ingest", data / "logs", "INFO", uuid.UUID(uid), 3)]


@pytest.mark.django_db
def test_ingest_exits_one_when_a_mission_failed(setup: tuple[Path, Path, FakeSteps]) -> None:
    _, logs, steps = setup
    write_parts(logs, A, ["a"], age_s=0, now=LONG_AGO, end=False)
    steps.fail_on = {A}
    assert main(["ingest"]) == EXIT_FAILED


@pytest.mark.django_db
def test_ingest_from_imports_and_never_touches_the_source(
    setup: tuple[Path, Path, FakeSteps], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, steps = setup
    source = tmp_path / "import"
    zip_path = write_zip(source / f"missionReport({B})[0].txt.zip", B, "T:0 AType:15\r\n")
    assert main(["ingest", "--from", str(source)]) == EXIT_OK
    assert zip_path.is_file()
    assert [uid for uid, _ in steps.parsed] == [B]
    assert "ingested 1" in capsys.readouterr().out


def test_ingest_without_a_log_dir_is_a_usage_error(
    setup: tuple[Path, Path, FakeSteps], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("IL2KS_LOGS_DIR")
    assert main(["ingest"]) == EXIT_USAGE
    assert "logs.dir is not configured" in capsys.readouterr().err


def test_ingest_from_a_missing_path_is_a_usage_error(
    setup: tuple[Path, Path, FakeSteps], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["ingest", "--from", str(tmp_path / "nope")]) == EXIT_USAGE
    assert "does not exist" in capsys.readouterr().err


def test_a_bad_config_value_is_a_usage_error(
    setup: tuple[Path, Path, FakeSteps], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("IL2KS_LOGS_AFTER_ARCHIVE", "shred")
    assert main(["ingest"]) == EXIT_USAGE
    assert "configuration error" in capsys.readouterr().err


@pytest.mark.django_db
def test_a_second_writer_exits_with_the_lock_code_and_a_message(
    setup: tuple[Path, Path, FakeSteps], capsys: pytest.CaptureFixture[str]
) -> None:
    data, _, _ = setup
    with WriterLock(data, "watch"):
        assert main(["ingest"]) == EXIT_LOCKED
    assert "another il2ks writer is running" in capsys.readouterr().err


@pytest.mark.django_db
def test_reprocess_and_rebuild_aggregates_run_on_an_empty_db(
    setup: tuple[Path, Path, FakeSteps], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["reprocess", "--workers", "1"]) == EXIT_OK
    assert "reprocessed 0" in capsys.readouterr().out
    assert main(["rebuild-aggregates"]) == EXIT_OK
    assert "rebuilt" in capsys.readouterr().out


@pytest.mark.django_db
def test_reprocess_of_an_unknown_mission_exits_one(
    setup: tuple[Path, Path, FakeSteps], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["reprocess", "--mission", A]) == EXIT_FAILED
    assert A in capsys.readouterr().out


@pytest.mark.django_db
def test_watch_stops_cleanly_on_ctrl_c(setup: tuple[Path, Path, FakeSteps], monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(cfg: Config, pipeline: runner.Pipeline) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(watch_mod, "watch", interrupted)
    assert main(["watch"]) == EXIT_OK


@pytest.mark.django_db
def test_reprocess_passes_the_date_span_and_missions_through(
    setup: tuple[Path, Path, FakeSteps], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[list[str] | None, date | None, date | None]] = []

    def fake_reprocess(
        cfg: Config,
        pipeline: runner.Pipeline,
        mission_uids: Sequence[str] | None = None,
        *,
        since: date | None = None,
        until: date | None = None,
        workers: int | None = None,
        lock_wait: float | None = None,
    ) -> reprocess_mod.ReprocessSummary:
        calls.append((None if mission_uids is None else list(mission_uids), since, until))
        return reprocess_mod.ReprocessSummary()

    monkeypatch.setattr(reprocess_mod, "reprocess", fake_reprocess)
    assert main(["reprocess", "--since", "2026-04-01", "--until", "2026-09-30", "--mission", A]) == EXIT_OK
    assert main(["reprocess", "--since", "2026-04-01"]) == EXIT_OK
    assert main(["reprocess"]) == EXIT_OK
    assert calls == [
        ([A], date(2026, 4, 1), date(2026, 9, 30)),
        (None, date(2026, 4, 1), None),
        (None, None, None),
    ]


def test_reprocess_since_after_until_is_a_usage_error(
    setup: tuple[Path, Path, FakeSteps], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["reprocess", "--since", "2026-05-01", "--until", "2026-04-01"]) == EXIT_USAGE
    assert "--since 2026-05-01 is after --until 2026-04-01" in capsys.readouterr().err


@pytest.mark.parametrize("text", ["yesterday", "2026-13-01", "20260401", "2026-4-1", "2026-W14-3", ""])
def test_reprocess_rejects_anything_but_yyyy_mm_dd(text: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["reprocess", "--since", text])
    assert info.value.code == 2
    assert "expected YYYY-MM-DD" in capsys.readouterr().err


def test_reprocess_help_documents_the_server_local_date(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["reprocess", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "--since YYYY-MM-DD" in out
    assert "--until YYYY-MM-DD" in out
    assert "server's local time (not UTC)" in out
