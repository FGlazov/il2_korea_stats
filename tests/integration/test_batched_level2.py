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
from pathlib import Path

import pytest
from django.db import transaction

from il2ks.config import Config
from il2ks.core.ratings.elo import DEFAULT_RULES, RatingRules
from il2ks.core.stat_marks import DEFAULT_MARK_RULES, MarkRules
from il2ks.db.models import Mission
from il2ks.ingest import batch as batch_mod
from il2ks.ingest import persist, runner
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.batch import Level2Batch, is_batch
from il2ks.ingest.persist import Touched
from il2ks.ingest.reprocess import reprocess
from il2ks.ingest.runner import IngestOptions, Pipeline, default_pipeline, ingest_once
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


def _spy(monkeypatch: pytest.MonkeyPatch, name: str) -> list[int]:
    calls: list[int] = []
    original: Callable[..., object] = getattr(persist, name)

    def spy(*args: object, **kwargs: object) -> object:
        calls.append(len(calls) + 1)
        return original(*args, **kwargs)

    monkeypatch.setattr(persist, name, spy)
    return calls


def test_small_batch_keeps_the_per_mission_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    level2 = _spy(monkeypatch, "apply_level2")
    ratings = _spy(monkeypatch, "recompute_ratings")
    end = _spy(monkeypatch, "apply_batch_end")
    cfg = make_config(tmp_path / "data", None, after_archive="keep")

    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))  # 5 < BATCH_MIN

    assert len(level2) == len(FIXTURES)
    assert len(ratings) == len(FIXTURES)
    assert end == []


def test_batched_run_does_ratings_once_at_the_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batch_mod, "BATCH_MIN", 2)
    ratings = _spy(monkeypatch, "recompute_ratings")
    end = _spy(monkeypatch, "apply_batch_end")
    cfg = make_config(tmp_path / "data", None, after_archive="keep")

    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))

    assert len(ratings) == 1
    assert len(end) == 1


def test_batch_threshold_is_twenty_missions() -> None:
    assert not is_batch(19)
    assert is_batch(20)


def test_level2_is_applied_at_every_tenth_and_at_the_end(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    batch = Level2Batch(30, DEFAULT_RULES, DEFAULT_MARK_RULES)

    def level2(touched: Touched, *, payload_elo: bool = True, holders: bool = True) -> None:
        assert (payload_elo, holders) == (False, False)  # those come once, at the end
        events.append(f"level2@{batch.done}")

    def end(touched: Touched, tours: Iterable[int], ratings: RatingRules, marks: MarkRules) -> None:
        assert 1 in set(tours)
        events.append(f"end@{batch.done}")

    monkeypatch.setattr(persist, "apply_level2", level2)
    monkeypatch.setattr(persist, "apply_batch_end", end)

    for n in range(30):
        batch.add(Touched(players={n}, tours={1}))
        batch.mission_done()
    batch.finish()
    batch.finish()  # idempotent

    assert events == [f"level2@{n}" for n in range(3, 30, 3)] + ["end@30"]


def test_a_batch_that_saved_nothing_applies_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    batch = Level2Batch(20, DEFAULT_RULES, DEFAULT_MARK_RULES)

    def level2(touched: Touched, *, payload_elo: bool = True, holders: bool = True) -> None:
        events.append("level2")

    def end(touched: Touched, tours: Iterable[int], ratings: RatingRules, marks: MarkRules) -> None:
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

    def save_level1(result: object, meta: persist.MissionMeta) -> tuple[Mission, Touched]:
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
