"""End to end: real parser, catalog, replay and persist over the anonymized fixture missions (doc 08).

Covers `ingest --from` (FR-ING-13), idempotency (FR-ING-6), incremental == rebuilt level 2 (TD-08), and `reprocess`
keeping PKs (FR-ING-9, FR-WEB-13).
"""

import shutil
from collections.abc import Mapping
from concurrent.futures import Executor, ThreadPoolExecutor
from pathlib import Path

import pytest

from il2ks.config import Config
from il2ks.db.models import IngestRun, IngestStatus, Kill, Mission, Player, PlayerAircraft, PlayerSortie
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.reprocess import reprocess
from il2ks.ingest.runner import IngestOptions, default_pipeline, ingest_once
from tests.conftest import FIXTURE_LOGS
from tests.ingest_fakes import make_config

pytestmark = pytest.mark.django_db

FIXTURES = {
    "typical": "2026-09-01_10-00-00",
    "most_bailouts": "2026-09-02_10-00-00",
    "two_mission_ends": "2026-09-03_10-00-00",
    "no_mission_end": "2026-09-04_10-00-00",
}

type Totals = Mapping[str, tuple[object, ...]]


def _import_dir(tmp_path: Path) -> Path:
    """Copy the fixtures under DServer-style names, so grouping recognizes them as whole-mission archives."""
    src = tmp_path / "import"
    src.mkdir()
    for name, uid in FIXTURES.items():
        shutil.copy(FIXTURE_LOGS / f"{name}.txt.zip", src / f"missionReport({uid})[0].txt.zip")
    return src


def _config(tmp_path: Path) -> Config:
    return make_config(tmp_path / "data", None, after_archive="keep")


def _level2() -> Totals:
    players = {
        f"p:{p.account_uuid}": (
            p.sorties,
            p.kills_air,
            p.kills_ground,
            p.assists,
            p.deaths,
            p.planes_lost,
            p.bailouts,
            p.landings,
            round(p.flight_time_s, 3),
            p.current_name,
        )
        for p in Player.objects.all()
    }
    aircraft = {
        f"a:{pa.player_id}:{pa.aircraft_id}": (pa.sorties, pa.kills_air, pa.deaths, round(pa.flight_time_s, 3))
        for pa in PlayerAircraft.objects.all()
    }
    return {**players, **aircraft}


def _thread_pool(workers: int) -> Executor:
    return ThreadPoolExecutor(max_workers=workers)


def test_import_fixtures_end_to_end(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    source = _import_dir(tmp_path)

    summary = ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=source))

    assert summary.failed == [], [r.error for r in IngestRun.objects.filter(status=IngestStatus.FAILED)]
    assert sorted(summary.ok) == sorted(FIXTURES.values())
    assert Mission.objects.count() == len(FIXTURES)
    assert PlayerSortie.objects.count() > 0
    assert Player.objects.count() > 0
    runs = list(IngestRun.objects.all())
    assert all(r.status == IngestStatus.OK and r.lines_bad == 0 and r.lines_total > 0 for r in runs)
    assert all((cfg.data_dir / r.archive_path).is_file() for r in runs)
    assert Mission.objects.get(mission_uid=FIXTURES["typical"]).completed_cleanly
    assert not Mission.objects.get(mission_uid=FIXTURES["no_mission_end"]).completed_cleanly
    assert all(s.damage_taken <= 1 for s in PlayerSortie.objects.all())
    # The import source is never touched (FR-ING-13, doc 04).
    assert len(list(source.iterdir())) == len(FIXTURES)


def test_second_import_is_a_no_op(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    source = _import_dir(tmp_path)
    ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=source))
    before = (_level2(), sorted(PlayerSortie.objects.values_list("pk", flat=True)), IngestRun.objects.count())

    summary = ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=source))

    assert summary.ok == []
    assert summary.failed == []
    assert (_level2(), sorted(PlayerSortie.objects.values_list("pk", flat=True)), IngestRun.objects.count()) == before


def test_incremental_level2_equals_rebuild(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
    incremental = _level2()

    rebuild_aggregates()

    assert _level2() == incremental


def test_reprocess_keeps_primary_keys_and_totals(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
    missions = dict(Mission.objects.values_list("mission_uid", "pk"))
    sorties = sorted(PlayerSortie.objects.values_list("pk", flat=True))
    kills = Kill.objects.count()
    totals = _level2()

    summary = reprocess(cfg, default_pipeline(cfg), workers=2, executor_factory=_thread_pool)

    assert sorted(summary.ok) == sorted(FIXTURES.values())
    assert dict(Mission.objects.values_list("mission_uid", "pk")) == missions
    assert sorted(PlayerSortie.objects.values_list("pk", flat=True)) == sorties
    assert Kill.objects.count() == kills
    assert _level2() == totals
