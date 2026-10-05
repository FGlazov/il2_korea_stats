"""Review #12 regressions: the rules the admin applied reach every path (live discard, the end of a long run, the
parser's limits, the effect texts, the wanted-vs-applied race). FR-ADM-7."""

import shutil
from collections.abc import Generator, Iterable
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import transaction

from il2ks.core.ratings.elo import DEFAULT_RULES
from il2ks.core.stat_marks import DEFAULT_MARK_RULES, MarkRules
from il2ks.db.models import SiteSettings, Tour
from il2ks.db.site import get_site_settings
from il2ks.ingest import aggregates, persist
from il2ks.ingest import batch as batch_mod
from il2ks.ingest.batch import Level2Batch
from il2ks.ingest.live import discard_stale_provisional
from il2ks.ingest.persist import save_mission
from il2ks.ingest.reprocess import reprocess
from il2ks.ingest.runner import IngestOptions, default_pipeline, ingest_once
from il2ks.ingest.stat_marks import recompute_thresholds
from il2ks.ingest.tours import start_manual_tour
from tests.conftest import FIXTURE_LOGS
from tests.factories import SERVER_UID, FakeCatalog, meta
from tests.ingest_fakes import make_config
from tests.integration.test_batched_level2 import FIXTURES
from tests.integration.test_tours import MONTHLY
from tests.integration.test_tours_decisive import at
from tests.integration.test_tours_review11 import result

pytestmark = pytest.mark.django_db


class _Rollback(Exception):
    pass


@contextmanager
def _scratch() -> Generator[None]:
    """Whatever happens inside is rolled back afterwards."""
    try:
        with transaction.atomic():
            yield
            raise _Rollback
    except _Rollback:
        pass


def _import_dir(tmp_path: Path) -> Path:
    src = tmp_path / "import"
    if not src.exists():
        src.mkdir()
        for name, uid in FIXTURES.items():
            shutil.copy(FIXTURE_LOGS / f"{name}.txt.zip", src / f"missionReport({uid})[0].txt.zip")
    return src


def _thread_pool(workers: int) -> Executor:
    return ThreadPoolExecutor(max_workers=workers)


def test_a_live_discard_uses_the_applied_tour_mode_not_the_files(tmp_path: Path) -> None:
    """Admin applied `tours.mode = manual` over a monthly file: discarding a stale provisional mission must not delete
    the admin's manual tour (FR-ADM-7: every path reads `effective_config`)."""
    get_site_settings()
    SiteSettings.objects.filter(pk=1).update(
        rule_settings={"tours.mode": "manual"}, rule_settings_applied={"tours.mode": "manual"}
    )
    manual = replace(MONTHLY, mode="manual")
    t0 = at(2026, 10, 1, 8)
    with transaction.atomic():
        save_mission(result(0, None), meta("m-1", t0), FakeCatalog(), DEFAULT_RULES, manual)
    start_manual_tour(t0 + timedelta(days=2), "Tour 2")
    live_meta = replace(meta("live-1", t0 + timedelta(days=3)), live=True)
    with transaction.atomic():
        save_mission(result(1, None), live_meta, FakeCatalog(), None, manual)
    assert Tour.objects.count() == 2
    cfg = replace(make_config(tmp_path, None), server_uid=SERVER_UID)  # the file says monthly (the default)
    assert discard_stale_provisional(cfg, keep="") == 1
    assert Tour.objects.count() == 2, "the admin's manual tour was deleted"


# --- a long run reads the marks in force when it computes the thresholds -------------------------------------------


def _spy_on_the_thresholds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The marks minimum of every threshold pass (the fixtures have too few pilots for a stored row to show it)."""
    minimums: list[int] = []

    def spy(rules: MarkRules = DEFAULT_MARK_RULES, tour_ids: Iterable[int] | None = None) -> None:
        minimums.append(rules.min_sorties)
        recompute_thresholds(rules, tour_ids)

    monkeypatch.setattr(persist, "recompute_thresholds", spy)
    monkeypatch.setattr(aggregates, "recompute_thresholds", spy)
    return minimums


def _admin_saves_marks_minimum(value: int) -> None:
    """What the Leaderboards page does for a display field: wanted and applied at once."""
    SiteSettings.objects.update_or_create(
        pk=1,
        defaults={"rule_settings": {"marks.min_sorties": value}, "rule_settings_applied": {"marks.min_sorties": value}},
    )


def test_a_batched_ingest_uses_the_marks_minimum_saved_during_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The admin saves a new marks minimum between two missions of a long run: the thresholds written at the end use it,
    not the minimum the run started with (FR-ADM-7)."""
    monkeypatch.setattr(batch_mod, "BATCH_MIN", 2)
    original = Level2Batch.mission_done
    seen: list[int] = []

    def saves_once_the_first_is_done(self: Level2Batch) -> None:
        original(self)
        if not seen:
            seen.append(1)
            _admin_saves_marks_minimum(1)

    monkeypatch.setattr(Level2Batch, "mission_done", saves_once_the_first_is_done)
    minimums = _spy_on_the_thresholds(monkeypatch)
    cfg = make_config(tmp_path / "data", None, after_archive="keep")
    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
    assert minimums[-1] == 1, minimums


def test_a_batched_reprocess_uses_the_marks_minimum_saved_during_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_config(tmp_path / "data", None, after_archive="keep")
    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
        monkeypatch.setattr(batch_mod, "BATCH_MIN", 2)
        saved: list[int] = []

        def saves_after_the_first(_summary: object) -> None:
            if not saved:
                saved.append(1)
                _admin_saves_marks_minimum(1)

        minimums = _spy_on_the_thresholds(monkeypatch)
        reprocess(
            cfg,
            default_pipeline(cfg, defer_ratings=True),
            workers=1,
            executor_factory=_thread_pool,
            on_progress=saves_after_the_first,
        )
    assert minimums[-1] == 1, minimums
