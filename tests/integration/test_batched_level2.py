"""Batched level 2 for long runs (roadmap "Batched level 2", doc 14): a batch of BATCH_MIN+ missions saves level 1 per
mission and applies level 2 at every 10% and at the end; ratings, holders and thresholds once at the end.

The end state must equal the per-mission path and a full rebuild. Each run happens inside a transaction that is rolled
back after the dump is taken, so one test can compare several ways of reaching the state. Rows are compared through
`tests.db_canon` (primary-key free, sorted, floats rounded: Postgres returns rows in any order and SUM order differs).
"""

import shutil
from collections.abc import Callable, Generator, Iterable
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
from django.db import transaction

from il2ks.config import Config
from il2ks.core.catalog.loader import load_default_catalog
from il2ks.core.logparse.files import MissionLog
from il2ks.core.logparse.parser import ParseStats
from il2ks.core.ratings.elo import DEFAULT_RULES, RatingRules
from il2ks.core.stat_marks import DEFAULT_MARK_RULES, MarkRules
from il2ks.core.tours import TourRules
from il2ks.db.models import Mission, SiteSettings, Tour
from il2ks.db.site import get_site_settings, level2_pending
from il2ks.ingest import aggregates, persist, runner
from il2ks.ingest import batch as batch_mod
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.batch import Level2Batch, is_batch
from il2ks.ingest.reprocess import reprocess
from il2ks.ingest.runner import IngestOptions, Pipeline, default_pipeline, ingest_once
from il2ks.ops import checks
from il2ks.ops.doctor import Level
from tests.conftest import FIXTURE_LOGS
from tests.db_canon import canonical_dump, diff_dumps
from tests.ingest_fakes import make_config

pytestmark = pytest.mark.django_db

FIXTURES = {
    "typical": "2026-09-01_10-00-00",
    "most_bailouts": "2026-09-02_10-00-00",
    "two_mission_ends": "2026-09-03_10-00-00",
    "no_mission_end": "2026-09-04_10-00-00",
    "bailout_ejection_stale_wheels": "2026-09-05_10-00-00",
}
type Dump = dict[str, list[str]]


class _Rollback(Exception):
    pass


def _import_dir(tmp_path: Path) -> Path:
    src = tmp_path / "import"
    if not src.exists():
        src.mkdir()
        for name, uid in FIXTURES.items():
            shutil.copy(FIXTURE_LOGS / f"{name}.txt.zip", src / f"missionReport({uid})[0].txt.zip")
    return src


@contextmanager
def _scratch() -> Generator[None]:
    """Whatever happens inside is rolled back afterwards."""
    try:
        with transaction.atomic():
            yield
            raise _Rollback
    except _Rollback:
        pass


def _run(tmp_path: Path, name: str, batch_min: int, monkeypatch: pytest.MonkeyPatch) -> tuple[Dump, Dump]:
    """Ingest the fixtures into a fresh data dir with this batch threshold: the dump, and the dump after a rebuild."""
    monkeypatch.setattr(batch_mod, "BATCH_MIN", batch_min)
    cfg = make_config(tmp_path / name, None, after_archive="keep")
    with _scratch():
        summary = ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
        assert summary.failed == []
        assert sorted(summary.ok) == sorted(FIXTURES.values())
        ingested = canonical_dump()
        _rebuild(cfg)
        rebuilt = canonical_dump()
    return ingested, rebuilt


def _rebuild(cfg: Config) -> None:
    rebuild_aggregates(cfg.ratings, cfg.tours, marks=cfg.marks, score=cfg.score, board=cfg.board)


def test_batched_ingest_equals_per_mission_and_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    per_mission, per_mission_rebuilt = _run(tmp_path, "one", 1000, monkeypatch)
    batched, batched_rebuilt = _run(tmp_path, "two", 2, monkeypatch)

    assert diff_dumps(per_mission, per_mission_rebuilt) == []
    assert diff_dumps(batched, batched_rebuilt) == []
    assert diff_dumps(per_mission, batched) == []
    assert any(per_mission["PlayerAircraft"])  # something was compared


def _spy(monkeypatch: pytest.MonkeyPatch, name: str, module: ModuleType = persist) -> list[int]:
    calls: list[int] = []
    original: Callable[..., object] = getattr(module, name)

    def spy(*args: object, **kwargs: object) -> object:
        calls.append(len(calls) + 1)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, spy)
    return calls


def test_small_batch_keeps_the_per_mission_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    level2 = _spy(monkeypatch, "apply_level2")
    ratings = _spy(monkeypatch, "recompute_ratings", aggregates)
    end = _spy(monkeypatch, "apply_batch_end")
    cfg = make_config(tmp_path / "data", None, after_archive="keep")

    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))  # 5 < BATCH_MIN

    assert len(level2) == len(FIXTURES)
    assert len(ratings) == len(FIXTURES)
    assert end == []


def test_batched_run_replays_only_the_touched_tours_and_ends_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every pass replays the Elo of the tours it refreshes (their medals read it), never every tour; one end."""
    monkeypatch.setattr(batch_mod, "BATCH_MIN", 2)
    replayed: list[object] = []
    original = aggregates.recompute_ratings

    def replay(
        rules: RatingRules, tour_ids: Iterable[int] | None = None, *, payload_elo: bool = True, all_time: bool = True
    ) -> int:
        replayed.append(None if tour_ids is None else sorted(tour_ids))
        return original(rules, tour_ids, payload_elo=payload_elo, all_time=all_time)

    monkeypatch.setattr(aggregates, "recompute_ratings", replay)
    end = _spy(monkeypatch, "apply_batch_end")
    cfg = make_config(tmp_path / "data", None, after_archive="keep")

    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))

    assert replayed
    assert None not in replayed
    assert len(end) == 1


def test_batch_threshold_is_twenty_missions() -> None:
    assert not is_batch(19)
    assert is_batch(20)


def test_level2_is_applied_at_every_tenth_and_at_the_end(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    batch = Level2Batch(30, DEFAULT_RULES, DEFAULT_MARK_RULES)

    def level2(
        tour_ids: Iterable[int], ratings: RatingRules | None, *, payload_elo: bool = True, holders: bool = True
    ) -> None:
        assert ratings is not None  # the tours' medals read the replay, so every pass has one
        assert (payload_elo, holders) == (False, False)  # those come once, at the end
        events.append(f"level2@{batch.done}")

    def end(tour_ids: Iterable[int], tours: Iterable[int], ratings: RatingRules, marks: MarkRules) -> None:
        assert 1 in set(tours)
        events.append(f"end@{batch.done}")

    monkeypatch.setattr(persist, "apply_level2", level2)
    monkeypatch.setattr(persist, "apply_batch_end", end)

    for _ in range(30):
        batch.add({1})
        batch.mission_done()
    batch.finish()
    batch.finish()  # idempotent

    assert events == [f"level2@{n}" for n in range(3, 30, 3)] + ["end@30"]


def test_a_batch_that_saved_nothing_applies_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    batch = Level2Batch(20, DEFAULT_RULES, DEFAULT_MARK_RULES)

    def level2(
        tour_ids: Iterable[int], ratings: RatingRules | None, *, payload_elo: bool = True, holders: bool = True
    ) -> None:
        events.append("level2")

    def end(tour_ids: Iterable[int], tours: Iterable[int], ratings: RatingRules, marks: MarkRules) -> None:
        events.append("end")

    monkeypatch.setattr(persist, "apply_level2", level2)
    monkeypatch.setattr(persist, "apply_batch_end", end)

    for _ in range(20):
        batch.mission_done()  # every mission failed
    batch.finish()

    assert events == []


def test_interrupted_batch_still_ends_in_a_consistent_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-C (or any non-`Exception`) in the middle: the `finally` applies what is pending, so the database equals a
    rebuild of the missions saved so far."""
    monkeypatch.setattr(batch_mod, "BATCH_MIN", 2)
    cfg = make_config(tmp_path / "data", None, after_archive="keep")
    real = default_pipeline(cfg)
    saved: list[str] = []
    assert real.save_level1 is not None
    real_save = real.save_level1

    def save_level1(result: object, meta: persist.MissionMeta) -> tuple[Mission, set[int]]:
        if len(saved) == 3:
            raise KeyboardInterrupt
        saved.append(meta.mission_uid)
        return real_save(result, meta)  # pyright: ignore[reportArgumentType]

    pipeline = Pipeline(real.group, real.parse, real.replay, real.save, real.resolve_start, save_level1)

    with _scratch():
        with pytest.raises(KeyboardInterrupt):
            ingest_once(cfg, pipeline, IngestOptions(source=_import_dir(tmp_path)))
        assert Mission.objects.count() == 3
        interrupted = canonical_dump()
        _rebuild(cfg)
        rebuilt = canonical_dump()

    assert diff_dumps(interrupted, rebuilt) == []


def test_a_killed_batch_is_repaired_by_rebuild_aggregates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hard kill loses the pending set: level 1 is saved, level 2 is behind. `rebuild-aggregates` repairs it, and the
    result equals the per-mission path."""
    expected, _ = _run(tmp_path, "one", 1000, monkeypatch)
    monkeypatch.setattr(batch_mod, "BATCH_MIN", 2)

    def nothing(self: Level2Batch) -> None:  # the process was killed: nothing after the saves ran
        return None

    monkeypatch.setattr(Level2Batch, "flush", nothing)
    monkeypatch.setattr(Level2Batch, "finish", nothing)
    cfg = make_config(tmp_path / "killed", None, after_archive="keep")

    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
        stale = canonical_dump()
        _rebuild(cfg)
        repaired = canonical_dump()

    assert diff_dumps(stale, expected) != []
    assert diff_dumps(repaired, expected) == []


def _no_migrations(_path: Path) -> set[tuple[str, str]]:
    return set()


def _nothing(_self: Level2Batch) -> None:
    return None


def _pending_findings(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The doctor's ingestion-check warnings about level 2 (the check needs a database file; the test one is used)."""
    monkeypatch.setattr(checks, "applied_migrations", _no_migrations)
    return [f.title for f in checks.ingestion_check(cfg) if f.level == Level.WARN and "level 2" in f.title.lower()]


def test_a_killed_batch_is_detected_by_doctor_and_repaired_by_the_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hard kill mid-batch leaves a marker: doctor warns with the fix, and the next ingest (writer lock held)
    rebuilds level 2 first, so the state equals the per-mission path, and clears the marker."""
    expected, _ = _run(tmp_path, "one", 1000, monkeypatch)
    monkeypatch.setattr(batch_mod, "BATCH_MIN", 2)
    monkeypatch.setattr(Level2Batch, "flush", _nothing)  # the process was killed: nothing after the saves ran
    monkeypatch.setattr(Level2Batch, "finish", _nothing)
    cfg = make_config(tmp_path / "killed", None, after_archive="keep")

    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
        assert diff_dumps(canonical_dump(), expected) != []
        [title] = _pending_findings(cfg, monkeypatch)
        assert "rebuild-aggregates" in "".join(
            f.fix for f in checks.ingestion_check(cfg) if "level 2" in f.title.lower()
        )
        monkeypatch.undo()  # the restarted process: a normal one
        summary = ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
        assert summary.ok == []  # every mission is unchanged: only the repair ran
        repaired = canonical_dump()
        monkeypatch.setattr(checks, "applied_migrations", _no_migrations)
        assert _pending_findings(cfg, monkeypatch) == []

    assert title
    assert diff_dumps(repaired, expected) == []


def test_a_finished_batch_leaves_no_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batch_mod, "BATCH_MIN", 2)
    cfg = make_config(tmp_path / "data", None, after_archive="keep")
    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
        assert _pending_findings(cfg, monkeypatch) == []


def test_a_killed_batched_reprocess_is_repaired_by_the_next_reprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_config(tmp_path / "data", None, after_archive="keep")
    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
        expected = canonical_dump()
        monkeypatch.setattr(batch_mod, "BATCH_MIN", 2)

        def killed() -> None:
            raise SystemExit  # not a rebuild: stands for the process dying before the final rebuild

        with pytest.raises(SystemExit):
            reprocess(
                cfg, default_pipeline(cfg, defer_ratings=True), workers=2, executor_factory=_thread_pool, rebuild=killed
            )
        assert len(_pending_findings(cfg, monkeypatch)) == 1
        reprocess(
            cfg, default_pipeline(cfg, defer_ratings=True), mission_uids=[], workers=2, executor_factory=_thread_pool
        )
        assert _pending_findings(cfg, monkeypatch) == []
        assert diff_dumps(canonical_dump(), expected) == []


def _thread_pool(workers: int) -> Executor:
    return ThreadPoolExecutor(max_workers=workers)


def _reprocess_dump(tmp_path: Path, batch_min: int, monkeypatch: pytest.MonkeyPatch) -> tuple[Dump, Dump, int]:
    cfg = make_config(tmp_path / "data", None, after_archive="keep")
    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
        before = canonical_dump()
        monkeypatch.setattr(batch_mod, "BATCH_MIN", batch_min)
        level2 = _spy(monkeypatch, "apply_level2")  # counts from here: the ingest above is not included
        summary = reprocess(cfg, default_pipeline(cfg, defer_ratings=True), workers=2, executor_factory=_thread_pool)
        assert len(summary.ok) == len(FIXTURES)
        after = canonical_dump()
    return before, after, len(level2)


def test_batched_reprocess_equals_the_per_mission_one_without_the_redundant_level_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before, small, small_calls = _reprocess_dump(tmp_path, 1000, monkeypatch)
    _, big, big_calls = _reprocess_dump(tmp_path, 2, monkeypatch)

    assert diff_dumps(before, small) == []
    assert diff_dumps(before, big) == []
    assert small_calls == len(FIXTURES)  # today's path: level 2 per mission, then the rebuild
    assert big_calls == 0  # batched: the final rebuild is the only level-2 pass


def test_runner_exposes_the_level1_step() -> None:
    cfg = make_config(Path("unused"), None)
    assert runner.default_pipeline(cfg).save_level1 is not None


# --- tour-based refresh: a batch tracks only the touched tours (doc 14, maintainer 2026-10-05) ---------

DAYS_RULES = TourRules(mode="days", days=2, start=date(2026, 9, 1))
"""The five fixtures are on Sept 1..5: three two-day tours, where the monthly default has one."""


def _resave(cfg: Config, rules: TourRules) -> list[set[int]]:
    """Level-1 re-save of every ingested mission under other tour rules (the mission moves to another tour); returns
    the tour ids each save reports."""
    pipeline = default_pipeline(cfg)
    reported: list[set[int]] = []
    for name, uid in FIXTURES.items():
        started = Mission.objects.get(mission_uid=uid).started_at
        log = MissionLog(uid, "archive", (FIXTURE_LOGS / f"{name}.txt.zip",))
        result = pipeline.replay(pipeline.parse(log, ParseStats()))
        meta = persist.MissionMeta(cfg.server_uid, uid, started, "")
        reported.append(persist.save_level1(result, meta, load_default_catalog(), rules)[1])
    return reported


def test_a_batch_that_moves_missions_between_tours_refreshes_both_tours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-ingest that moved a mission reports its old and its new tour; refreshing exactly those tours equals a
    full rebuild (the old tour loses the mission's rows, the new one gains them)."""
    monkeypatch.setattr(batch_mod, "BATCH_MIN", 1000)
    cfg = make_config(tmp_path / "data", None, after_archive="keep")
    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
        batch = Level2Batch(len(FIXTURES), cfg.ratings, cfg.marks)
        batch.start()
        reported = _resave(cfg, DAYS_RULES)
        for tours in reported:
            batch.add(tours)
            batch.mission_done()
        batch.finish()
        batched = canonical_dump()
        _rebuild(cfg)
        rebuilt = canonical_dump()

    # the three missions after Sept 2 left tour 1 (monthly) for a new two-day tour: both ids are reported
    assert sum(len(tours) == 2 for tours in reported) == 3, reported
    assert len({tid for tours in reported for tid in tours}) == 3
    assert diff_dumps(batched, rebuilt) == []


def test_a_batch_over_several_tours_equals_the_per_mission_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(name: str, batch_min: int) -> Dump:
        monkeypatch.setattr(batch_mod, "BATCH_MIN", batch_min)
        cfg = replace(make_config(tmp_path / name, None, after_archive="keep"), tours=DAYS_RULES)
        with _scratch():
            ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
            return canonical_dump()

    per_mission = run("one", 1000)
    batched = run("two", 2)

    assert len(per_mission["Tour"]) == 3
    assert diff_dumps(per_mission, batched) == []


def test_the_marker_names_the_tours_of_the_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batch_mod, "BATCH_MIN", 2)
    monkeypatch.setattr(Level2Batch, "finish", _nothing)  # killed before the end
    cfg = replace(make_config(tmp_path / "data", None, after_archive="keep"), tours=DAYS_RULES)
    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
        tours = level2_pending()["tours"]
        assert tours == sorted(Tour.objects.values_list("pk", flat=True))
        assert len(Tour.objects.all()) == 3


def test_a_marker_without_tours_is_repaired_by_a_full_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A marker from a reprocess (or an older version) names no tours: the repair is the full rebuild."""
    expected, _ = _run(tmp_path, "one", 1000, monkeypatch)
    monkeypatch.setattr(batch_mod, "BATCH_MIN", 2)
    monkeypatch.setattr(Level2Batch, "flush", _nothing)
    monkeypatch.setattr(Level2Batch, "finish", _nothing)
    cfg = make_config(tmp_path / "killed", None, after_archive="keep")
    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
        SiteSettings.objects.filter(pk=1).update(level2_pending={"command": "reprocess", "since": "x"})
        monkeypatch.undo()
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
        repaired = canonical_dump()
        assert level2_pending() == {}

    assert diff_dumps(repaired, expected) == []


def test_a_rolled_back_save_does_not_make_the_marker_skip_its_tour() -> None:
    """The marker write is part of the save's transaction: when the save rolls back, the next save of the same tour
    writes the marker again (the batch must not believe the tour is already named)."""
    batch = Level2Batch(30, DEFAULT_RULES, DEFAULT_MARK_RULES)
    batch.start()

    def failing_save() -> None:
        with transaction.atomic():
            batch.add({7})
            raise _Rollback

    with pytest.raises(_Rollback):
        failing_save()
    assert not level2_pending().get("tours")  # rolled back with the save
    with transaction.atomic():
        batch.add({7})
    assert level2_pending()["tours"] == [7]
    batch.add({7})  # now stored: no second write needed, and still named
    assert level2_pending()["tours"] == [7]


def test_doctor_does_not_warn_while_a_batch_is_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The marker is set for the whole run of a healthy batch: while a live process holds the writer lock, doctor says
    "a batch is running" (not a warning); once the lock is free the marker means the run was killed (warning)."""
    from il2ks.ingest.lock import WriterLock

    cfg = make_config(tmp_path / "data", None, after_archive="keep")
    monkeypatch.setattr(checks, "applied_migrations", _no_migrations)
    get_site_settings()  # creates the row
    SiteSettings.objects.filter(pk=1).update(level2_pending={"command": "ingest", "since": "now", "pid": 1})
    with WriterLock(cfg.data_dir, "ingest"):
        findings = [
            f for f in checks.ingestion_check(cfg) if "batch" in f.title.lower() or "level 2" in f.title.lower()
        ]
        assert [f.level for f in findings] == [Level.OK]
        assert "running" in findings[0].title
    assert len(_pending_findings(cfg, monkeypatch)) == 1
