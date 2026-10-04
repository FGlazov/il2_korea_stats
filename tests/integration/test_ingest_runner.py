"""The ingest runner with fake parse/replay/persist steps (doc 04 pipeline, FR-ING-5, 8, 10, 18, 19, 20, NFR-REL-3)."""

from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from il2ks.config import AfterArchive, Config
from il2ks.core.logparse.parser import ParseStats
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import MissionResult
from il2ks.db.models import CompletionReason, IngestRun, IngestStatus, Mission
from il2ks.ingest import archive as archive_mod
from il2ks.ingest import runner
from il2ks.ingest.archive import iter_source_bytes
from il2ks.ingest.lock import LockBusyError, WriterLock
from il2ks.ingest.reprocess import rebuild_all, reprocess
from il2ks.ingest.runner import IngestOptions, IngestSummary, Pipeline, ingest_once
from il2ks.ingest.watch import watch
from tests.ingest_fakes import (
    SERVER_UID,
    T0,
    FakeSteps,
    make_config,
    make_pipeline,
    part_name,
    set_mtime,
    write_parts,
    write_zip,
)

pytestmark = pytest.mark.django_db

A, B, C = "2026-09-19_10-00-00", "2026-09-19_13-00-00", "2026-09-19_16-00-00"


class Env:
    """A temp data dir and log dir with a config, fakes and a controllable clock."""

    def __init__(self, tmp_path: Path, after_archive: AfterArchive = "move") -> None:
        self.data = tmp_path / "data"
        self.logs = tmp_path / "logs"
        self.logs.mkdir()
        self.cfg: Config = make_config(self.data, self.logs, after_archive=after_archive)
        self.steps = FakeSteps()
        self.pipeline: Pipeline = make_pipeline(self.steps)
        self.clock = T0

    def now(self) -> datetime:
        return self.clock

    def ingest(self, source: Path | None = None) -> IngestSummary:
        return ingest_once(self.cfg, self.pipeline, IngestOptions(source=source), now=self.now)

    def add(self, uid: str, texts: list[str] | None = None, *, age_s: float = 600, end: bool = True) -> list[Path]:
        return write_parts(self.logs, uid, texts or ["a", "b"], age_s=age_s, now=self.clock, end=end)

    def runs(self, uid: str) -> list[IngestRun]:
        return list(IngestRun.objects.filter(mission_uid=uid).order_by("id"))

    def archive_of(self, uid: str) -> Path:
        run = IngestRun.objects.filter(mission_uid=uid).exclude(archive_path="").latest("id")
        return self.data / run.archive_path


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def test_ok_run_is_archived_saved_recorded_and_originals_moved(env: Env) -> None:
    parts = env.add(A, ["a", "b", "c"])
    summary = env.ingest()

    assert summary.ok == [A]
    assert summary.failed == []
    (run,) = env.runs(A)
    assert run.status == IngestStatus.OK
    assert run.mission is not None
    assert run.files == [p.name for p in parts]
    assert run.attempts == 1
    assert run.next_retry_at is None
    assert run.error == ""
    assert (run.lines_total, run.lines_bad, run.log_version) == (7, 1, 18)
    assert run.unknown_atypes == {"99": 2}
    assert run.unknown_keys == {"12:FOO": 1}
    assert run.warnings == ["a bad line"]
    assert run.finished_at is not None
    assert run.archive_path == f"archive/2026/09/missionReport({A})[0].txt.zip"
    assert len(run.archive_sha256) == 64

    archived = b"".join(iter_source_bytes(env.data / run.archive_path))
    assert archived.count(b"AType:15") == 3
    assert env.steps.parsed == [(A, archived)]  # parsed from the archive, not from the originals

    mission = Mission.objects.get(mission_uid=A)
    assert mission.server_uid == SERVER_UID
    assert mission.started_at == datetime(2026, 9, 19, 10, 0, 0, tzinfo=UTC)
    assert env.steps.saved[0].archive_path == run.archive_path

    assert not any(p.exists() for p in parts)
    assert sorted(p.name for p in (env.data / "ingested-logs" / A).iterdir()) == sorted(p.name for p in parts)


def test_incomplete_missions_are_left_alone(env: Env) -> None:
    env.add(A, age_s=20, end=False)
    summary = env.ingest()
    assert summary.incomplete == [A]
    assert summary.ok == []
    assert env.runs(A) == []


def test_unchanged_mission_is_skipped_and_files_are_kept_in_keep_mode(tmp_path: Path) -> None:
    env = Env(tmp_path, after_archive="keep")
    parts = env.add(A)
    env.ingest()
    summary = env.ingest()
    assert summary.skipped == {"unchanged": [A]}
    assert len(env.runs(A)) == 1
    assert all(p.exists() for p in parts)
    assert len(env.steps.parsed) == 1


def test_changed_fingerprint_reingests_in_place(tmp_path: Path) -> None:
    env = Env(tmp_path, after_archive="keep")
    parts = env.add(A)
    env.ingest()
    parts[1].write_bytes(parts[1].read_bytes() + b"T:200 AType:6\r\n")  # a late write
    set_mtime(parts[1], T0 - timedelta(seconds=300))

    summary = env.ingest()

    assert summary.ok == [A]
    first, second = env.runs(A)
    assert first.fingerprint != second.fingerprint
    assert second.status == IngestStatus.OK
    assert Mission.objects.filter(mission_uid=A).count() == 1
    assert b"T:200" in b"".join(iter_source_bytes(env.archive_of(A)))


def test_late_part_after_originals_moved_extends_the_archive(env: Env) -> None:
    """FR-ING-18: the early parts are gone from the log folder; the new archive = old archive + the late part."""
    env.add(A, ["a", "b"])
    env.ingest()
    old = b"".join(iter_source_bytes(env.archive_of(A)))
    late = env.logs / part_name(A, 2)
    late.write_bytes(b"T:300 AType:6\r\n")
    set_mtime(late, T0 - timedelta(seconds=700))

    summary = env.ingest()

    assert summary.ok == [A]
    new = b"".join(iter_source_bytes(env.archive_of(A)))
    assert new == old + b"T:300 AType:6\r\n"
    last = env.runs(A)[-1]
    assert last.files == [part_name(A, 0), part_name(A, 1), part_name(A, 2)]
    assert not late.exists()
    assert Mission.objects.filter(mission_uid=A).count() == 1


def test_failure_is_recorded_with_backoff_and_does_not_block_the_next_mission(env: Env) -> None:
    parts_a = env.add(A)
    env.add(B)
    env.steps.fail_on = {A}

    summary = env.ingest()

    assert summary.failed == [A]
    assert summary.ok == [B]
    (failed,) = env.runs(A)
    assert failed.status == IngestStatus.FAILED
    assert "cannot save" in failed.error
    assert failed.attempts == 1
    assert failed.next_retry_at == T0 + timedelta(minutes=5)
    assert failed.mission is None
    assert not Mission.objects.filter(mission_uid=A).exists()  # nothing half-saved
    assert Mission.objects.filter(mission_uid=B).exists()
    assert all(p.exists() for p in parts_a)  # failed missions keep their originals
    assert failed.archive_path  # but the archive was written first


def test_backoff_is_respected_and_the_schedule_stops(env: Env) -> None:
    env.add(A)
    env.steps.fail_on = {A}
    env.ingest()

    env.clock = T0 + timedelta(minutes=4)
    assert env.ingest().skipped == {"backoff": [A]}
    assert len(env.runs(A)) == 1

    expected = [(2, timedelta(minutes=30)), (3, timedelta(hours=2))]
    for attempts, delay in expected:
        env.clock = env.runs(A)[-1].next_retry_at or env.clock
        assert env.ingest().failed == [A]
        last = env.runs(A)[-1]
        assert last.attempts == attempts
        assert last.next_retry_at == env.clock + delay

    env.clock = env.clock + timedelta(hours=3)
    assert env.ingest().failed == [A]  # attempt 4, the last one
    last = env.runs(A)[-1]
    assert last.attempts == 4
    assert last.next_retry_at is None

    env.clock = env.clock + timedelta(days=1)
    assert env.ingest().skipped == {"gave_up": [A]}
    assert len(env.runs(A)) == 4


def test_failed_mission_is_retried_at_once_when_its_files_change(env: Env) -> None:
    parts = env.add(A)
    env.steps.fail_on = {A}
    env.ingest()
    env.steps.fail_on = set()
    parts[0].write_bytes(parts[0].read_bytes() + b"T:1 AType:5\r\n")
    set_mtime(parts[0], T0 - timedelta(seconds=300))

    assert env.ingest().ok == [A]
    assert env.runs(A)[-1].attempts == 1


def test_failed_mission_is_retried_when_a_new_version_is_installed(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.add(A)
    env.steps.fail_on = {A}
    for _ in range(1):
        env.ingest()
    env.clock = T0 + timedelta(minutes=1)
    assert env.ingest().skipped == {"backoff": [A]}
    env.steps.fail_on = set()
    monkeypatch.setattr(runner, "__version__", "99.0.0")
    assert env.ingest().ok == [A]
    assert env.runs(A)[-1].il2ks_version == "99.0.0"


def test_reconcile_rebuilds_a_missing_archive_while_sources_exist(tmp_path: Path) -> None:
    """FR-ING-8: ingested, source files still there, archive gone -> archived again."""
    env = Env(tmp_path, after_archive="keep")
    env.add(A)
    env.ingest()
    env.archive_of(A).unlink()

    summary = env.ingest()

    assert summary.ok == [A]
    assert env.archive_of(A).is_file()
    assert len(env.runs(A)) == 2


def test_originals_that_could_not_be_moved_are_moved_on_the_next_run(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    parts = env.add(A)

    def fail_once(src: Path, dst: Path) -> None:
        raise PermissionError("in use")

    monkeypatch.setattr(archive_mod.shutil, "move", fail_once)
    summary = env.ingest()
    assert summary.ok == [A]
    assert summary.dispose_errors == len(parts)
    assert all(p.exists() for p in parts)
    monkeypatch.undo()

    summary = env.ingest()
    assert summary.skipped == {"unchanged": [A]}
    assert summary.disposed_files == len(parts)
    assert not any(p.exists() for p in parts)


def test_delete_mode_removes_originals(tmp_path: Path) -> None:
    env = Env(tmp_path, after_archive="delete")
    parts = env.add(A)
    env.ingest()
    assert not any(p.exists() for p in parts)
    assert not (env.data / "ingested-logs").exists()


def test_import_takes_a_folder_of_whole_mission_archives_and_never_touches_the_sources(tmp_path: Path) -> None:
    env = Env(tmp_path, after_archive="delete")  # config says delete; an import must ignore that
    source = tmp_path / "import"
    zip_a = write_zip(source / f"{part_name(A, 0)}.zip", A, "T:0 AType:15\r\nT:1 AType:5\r\n")
    plain_b = source / part_name(B, 0)
    plain_b.write_bytes(b"T:0 AType:15\r\nT:2 AType:6\r\n")
    set_mtime(plain_b, T0 - timedelta(seconds=1))  # fresh: only an import treats it as complete
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in source.iterdir()}

    summary = env.ingest(source=source)

    assert sorted(summary.ok) == [A, B]
    assert before == {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in source.iterdir()}
    assert zip_a.exists()
    assert plain_b.exists()
    assert env.archive_of(B).is_file()
    assert Mission.objects.count() == 2
    assert env.ingest(source=source).skipped == {"unchanged": [A, B]}


def test_import_of_a_single_file(tmp_path: Path) -> None:
    env = Env(tmp_path)
    file = tmp_path / part_name(A, 0)
    file.write_bytes(b"T:0 AType:15\r\n")
    assert env.ingest(source=file).ok == [A]
    assert file.exists()


def test_broken_archive_fails_that_mission_only(tmp_path: Path) -> None:
    env = Env(tmp_path)
    source = tmp_path / "import"
    source.mkdir()
    (source / f"{part_name(A, 0)}.zip").write_bytes(b"this is not a zip")
    write_zip(source / f"{part_name(B, 0)}.zip", B, "T:0 AType:15\r\n")
    summary = env.ingest(source=source)
    assert summary.failed == [A]
    assert summary.ok == [B]


def test_unconfigured_log_dir_is_an_error(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.cfg = make_config(env.data, None)
    with pytest.raises(ValueError, match=r"logs.dir"):
        env.ingest()


def test_second_writer_gets_a_clear_error(env: Env) -> None:
    with WriterLock(env.data, "watch"), pytest.raises(LockBusyError, match="watch"):
        env.ingest()


def test_failure_in_one_mission_is_isolated_by_the_transaction(env: Env) -> None:
    """The save raises after writing a row: the row must roll back (FR-ING-5)."""
    env.add(A)
    steps = env.steps
    inner = env.pipeline.save

    def save_then_fail(result: MissionResult, meta: runner.MissionMeta) -> Mission:
        inner(result, meta)
        raise RuntimeError("late failure")

    env.pipeline = Pipeline(
        group=env.pipeline.group,
        parse=env.pipeline.parse,
        replay=env.pipeline.replay,
        save=save_then_fail,
        resolve_start=env.pipeline.resolve_start,
    )
    assert env.ingest().failed == [A]
    assert not Mission.objects.filter(mission_uid=A).exists()
    assert steps.saved  # the fake did write before the failure


def test_resolve_start_warnings_are_recorded(env: Env) -> None:
    env.steps.warnings = ("ambiguous local time",)
    env.add(A)
    env.ingest()
    assert env.runs(A)[0].warnings == ["ambiguous local time", "a bad line"]


def test_completion_reason_is_recorded_per_run(env: Env, tmp_path: Path) -> None:
    """D4 / FR-ING-18: admins can see which missions were completed only by the idle timeout."""
    env.add(A, age_s=120)  # AType 7 and settled
    env.ingest()
    env.add(B, age_s=300, end=False)  # nothing marks the end, but ...
    env.add(C, age_s=20, end=False)  # ... a newer mission has started
    env.add("2026-09-19_17-00-00", age_s=700, end=False)  # idle for more than 10 minutes
    env.ingest()

    reasons = {r.mission_uid: r.completion_reason for r in IngestRun.objects.all()}
    assert reasons == {
        A: "mission_end",
        B: "newer_mission",
        C: "newer_mission",
        "2026-09-19_17-00-00": "idle",
    }

    source = tmp_path / "import"
    write_zip(source / f"{part_name('2026-09-18_10-00-00', 0)}.zip", "2026-09-18_10-00-00", "T:0 AType:15\r\n")
    env.ingest(source=source)
    assert env.runs("2026-09-18_10-00-00")[0].completion_reason == "import"


def test_whole_mission_archive_in_the_log_folder_counts_as_import(env: Env) -> None:
    write_zip(env.logs / f"{part_name(A, 0)}.zip", A, "T:0 AType:15\r\n")
    env.ingest()
    assert env.runs(A)[0].completion_reason == "import"


def test_failed_run_keeps_its_completion_reason(env: Env) -> None:
    env.steps.fail_on = {A}
    env.add(A, age_s=120)
    env.ingest()
    assert env.runs(A)[0].completion_reason == "mission_end"


def test_country_side_conflicts_are_recorded_as_warnings(env: Env) -> None:
    """D5: a CNTRS that puts 5xx and 6xx in one coalition is recorded on the run (the mission still ingests)."""
    env.steps.countries = {501: 1, 601: 1, 602: 2}
    env.add(A)
    summary = env.ingest()

    assert summary.ok == [A]
    assert env.runs(A)[0].warnings == ["coalition 1 mixes REDFOR and BLUFOR countries: [501, 601]", "a bad line"]


def test_normal_country_codes_add_no_warning(env: Env) -> None:
    env.add(A)
    env.ingest()
    assert env.runs(A)[0].warnings == ["a bad line"]


def fast(cfg: Config) -> Config:
    return replace(cfg, ingest=replace(cfg.ingest, watch_interval_s=0.01))


# --- watch -------------------------------------------------------------------------------------------------------


def test_watch_ticks_until_the_limit_and_ingests_new_missions(env: Env) -> None:
    env.add(A)
    ticks = watch(fast(env.cfg), env.pipeline, max_ticks=2, now=env.now)
    assert ticks == 2
    assert len(env.runs(A)) == 1  # the second tick skipped it


def test_watch_survives_a_busy_lock_and_a_crashing_tick(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.add(A)
    with WriterLock(env.data, "reprocess"):
        assert watch(fast(env.cfg), env.pipeline, max_ticks=1, now=env.now) == 1
    assert env.runs(A) == []

    def boom(*args: object, **kwargs: object) -> IngestSummary:
        raise RuntimeError("tick crashed")

    monkeypatch.setattr("il2ks.ingest.watch.ingest_once", boom)
    assert watch(fast(env.cfg), env.pipeline, max_ticks=2, now=env.now) == 2


# --- reprocess ---------------------------------------------------------------------------------------------------


def fake_work(uid: str, archive: Path, rules: ReplayRules) -> tuple[MissionResult, ParseStats]:
    assert archive.is_file()
    stats = ParseStats()
    stats.lines_total = 7
    return runner_result(), stats


def runner_result() -> MissionResult:
    from typing import cast

    return cast(MissionResult, object())


def threads(workers: int) -> Executor:
    return ThreadPoolExecutor(max_workers=workers)


def do_reprocess(
    env: Env, uids: list[str] | None = None, work: Callable[..., tuple[MissionResult, ParseStats]] = fake_work
) -> tuple[object, list[int]]:
    rebuilt: list[int] = []
    summary = reprocess(
        env.cfg,
        env.pipeline,
        uids,
        workers=2,
        work=work,
        executor_factory=threads,
        rebuild=lambda: rebuilt.append(1),
        now=env.now,
    )
    return summary, rebuilt


def test_reprocess_reruns_archived_missions_in_place_and_rebuilds_once(env: Env) -> None:
    env.add(A)
    env.add(B)
    env.ingest()
    mission_ids = set(Mission.objects.values_list("id", flat=True))

    summary, rebuilt = do_reprocess(env)

    # workers finish in any order, so the summary order is not part of the contract
    assert sorted(getattr(summary, "ok")) == [A, B]  # noqa: B009
    assert rebuilt == [1]
    assert set(Mission.objects.values_list("id", flat=True)) == mission_ids  # updated in place
    first_run, second_run = env.runs(A)
    assert second_run.status == IngestStatus.OK
    assert second_run.fingerprint == first_run.fingerprint  # discovery doesn't see a change
    assert second_run.lines_total == 7
    assert second_run.archive_sha256 == first_run.archive_sha256
    assert first_run.completion_reason != CompletionReason.REPROCESS
    assert second_run.completion_reason == CompletionReason.REPROCESS  # FR-ING-18


def test_reprocess_only_the_requested_missions_and_reports_missing_ones(env: Env) -> None:
    env.add(A)
    env.add(B)
    env.ingest()
    summary, rebuilt = do_reprocess(env, [B, "2020-01-01_00-00-00"])
    assert getattr(summary, "ok") == [B]  # noqa: B009
    assert getattr(summary, "missing") == ["2020-01-01_00-00-00"]  # noqa: B009
    assert len(env.runs(A)) == 1
    assert rebuilt == [1]


def test_reprocess_records_failures_without_stopping_and_without_retry_schedule(env: Env) -> None:
    env.add(A)
    env.add(B)
    env.ingest()

    def work(uid: str, archive: Path, rules: ReplayRules) -> tuple[MissionResult, ParseStats]:
        if uid == A:
            raise ValueError("replay bug")
        return fake_work(uid, archive, rules)

    summary, _ = do_reprocess(env, work=work)

    assert getattr(summary, "failed") == [A]  # noqa: B009
    assert getattr(summary, "ok") == [B]  # noqa: B009
    failed = env.runs(A)[-1]
    assert failed.status == IngestStatus.FAILED
    assert "replay bug" in failed.error
    assert failed.next_retry_at is None
    assert failed.completion_reason == CompletionReason.REPROCESS
    assert Mission.objects.filter(mission_uid=A).exists()  # the earlier good result stays


def test_reprocess_fails_a_mission_whose_archive_changed(env: Env) -> None:
    env.add(A)
    env.ingest()
    env.archive_of(A).write_bytes(b"corrupted")
    summary, _ = do_reprocess(env)
    assert getattr(summary, "failed") == [A]  # noqa: B009
    assert "missing or changed" in env.runs(A)[-1].error


def test_reprocess_rebuilds_a_lost_database_from_the_archives_alone(env: Env) -> None:
    """FR-ING-9: archives on disk with no IngestRun history are adopted."""
    env.add(A)
    env.add(B)
    env.ingest()
    IngestRun.objects.all().delete()
    Mission.objects.all().delete()

    summary, rebuilt = do_reprocess(env)

    assert sorted(getattr(summary, "ok")) == [A, B]  # noqa: B009
    assert rebuilt == [1]
    assert Mission.objects.count() == 2
    run = env.runs(A)[0]
    assert run.archive_path == f"archive/2026/09/missionReport({A})[0].txt.zip"
    assert len(run.archive_sha256) == 64
    assert run.fingerprint == ""


def test_reprocess_needs_the_writer_lock(env: Env) -> None:
    with WriterLock(env.data, "watch"), pytest.raises(LockBusyError):
        do_reprocess(env)


def test_rebuild_all_runs_under_the_lock(env: Env) -> None:
    calls: list[int] = []
    rebuild_all(env.cfg, rebuild=lambda: calls.append(1))
    assert calls == [1]
    with WriterLock(env.data, "watch"), pytest.raises(LockBusyError):
        rebuild_all(env.cfg, rebuild=lambda: calls.append(2))
    assert calls == [1]


def test_move_or_delete_refuses_a_sample_data_log_folder(tmp_path: Path) -> None:
    """Real player data lives in `sample_data/`; an ingest with `move` must never touch it."""
    logs = tmp_path / "sample_data" / "2026-09"
    logs.mkdir(parents=True)
    cfg = make_config(tmp_path / "data", logs, after_archive="move")
    with pytest.raises(ValueError, match="sample_data"):
        ingest_once(cfg, make_pipeline(FakeSteps()))
    keep = make_config(tmp_path / "data", logs, after_archive="keep")
    assert ingest_once(keep, make_pipeline(FakeSteps())).ok == []


def test_the_sample_data_guard_ignores_the_case_of_the_folder_name(tmp_path: Path) -> None:
    """Windows and macOS file systems are case-insensitive: `Sample_Data` is the same real player data."""
    logs = tmp_path / "Sample_Data" / "2026-09"
    logs.mkdir(parents=True)
    cfg = make_config(tmp_path / "data", logs, after_archive="move")
    with pytest.raises(ValueError, match="sample_data"):
        ingest_once(cfg, make_pipeline(FakeSteps()))
