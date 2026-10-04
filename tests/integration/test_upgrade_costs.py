"""Costs that must not grow with history: the upgrade backfills (one rebuild, partial writes), the per-mission medal
holder counts and the per-mission stat thresholds (pre-filtered rows)."""

import uuid
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from il2ks.config import Config
from il2ks.core.stat_marks import ELO_METRICS, METRICS, MarkRules, Totals, amount, metric_value, thresholds
from il2ks.db.models import (
    AchievementHolders,
    GameObject,
    Player,
    PlayerAchievement,
    PlayerSortie,
    PlayerTour,
    SiteSettings,
    StatThreshold,
    Tour,
)
from il2ks.ingest import aggregates
from il2ks.ingest.achievements import recompute_holders
from il2ks.ingest.stat_marks import _ELO_FIELDS, _FIELDS, recompute_thresholds  # pyright: ignore[reportPrivateUsage]
from il2ks.ops import migrate
from tests.factories import account, mission, save, sortie

pytestmark = pytest.mark.django_db

N = 12


def count_rebuilds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    def fake(*args: object, **kwargs: object) -> None:
        calls.append(1)

    monkeypatch.setattr(aggregates, "rebuild_aggregates", fake)
    return calls


def cfg() -> Config:
    return Config(data_dir=Path("."), server_uid=uuid.uuid4(), timezone_name="UTC")


def seed_old_database() -> list[int]:
    """N sorties with assists and kills, and timelines naming the victims; the new columns zeroed, markers cleared."""
    GameObject.objects.update_or_create(log_name="B-29", defaults={"display_name": "B-29", "cls": "bomber"})
    save(mission(tuple(sortie(i, i + 1, assists=2, kills_air=1) for i in range(N))))
    timeline = [
        {"kind": "assist", "counterpart": {"object_type": "B-29", "sortie_id": None, "coalition": 1}},
        {"kind": "assist", "counterpart": {"object_type": "M46 Patton", "sortie_id": None, "coalition": 1}},
        {"kind": "kill", "counterpart": {"object_type": "B-29", "sortie_id": None, "coalition": 1}},
    ]
    PlayerSortie.objects.update(timeline=timeline, assists_air=0, assists_ground=0, kills_air_intercept=0)
    SiteSettings.objects.filter(pk=1).update(backfills_done=[])
    return list(PlayerSortie.objects.values_list("pk", flat=True))


@pytest.mark.parametrize("name", [migrate.BACKFILL_ASSIST_SPLIT, migrate.BACKFILL_INTERCEPTION])
def test_the_sortie_backfills_write_partial_rows_without_refreshing_deferred_fields(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    count_rebuilds(monkeypatch)  # level 2 is not under test
    pks = seed_old_database()

    with CaptureQueriesContext(connection) as queries:
        migrate._run_backfills(cfg(), [name])  # pyright: ignore[reportPrivateUsage]

    # A fixed handful of reads, one UPDATE per changed sortie, and none of the per-field `refresh_from_db` SELECTs
    # (~66 per sortie) the upsert of `.only()` rows caused.
    sql = [q["sql"] for q in queries]
    selects = [s for s in sql if s.startswith("SELECT")]
    updates = [s for s in sql if s.startswith("UPDATE")]
    assert len(updates) >= len(pks)
    assert len(selects) < 12, len(selects)
    assert not any('"il2ks_db_playersortie"."id" = ' in s for s in selects)
    assert len(sql) < len(pks) + 20, len(sql)
    row = PlayerSortie.objects.get(pk=pks[0])
    if name == migrate.BACKFILL_ASSIST_SPLIT:
        assert (row.assists_air, row.assists_ground) == (1, 1)
    else:
        assert row.kills_air_intercept == 1
    assert row.timeline  # the timeline was not touched


def test_an_old_shaped_database_rebuilds_the_aggregates_once(monkeypatch: pytest.MonkeyPatch) -> None:
    seed_old_database()
    PlayerSortie.objects.update(air_points=0.0, ground_points=0.0)  # the scores backfill fires too
    calls = count_rebuilds(monkeypatch)

    migrate._run_backfills(cfg())  # pyright: ignore[reportPrivateUsage]

    assert calls == [1]
    done = SiteSettings.objects.get(pk=1).backfills_done
    for name in (
        migrate.BACKFILL_TOURS,
        migrate.BACKFILL_SCORES,
        migrate.BACKFILL_INTERCEPTION,
        migrate.BACKFILL_ASSIST_SPLIT,
        migrate.BACKFILL_ACHIEVEMENTS,
        migrate.BACKFILL_ACHIEVEMENT_TOURS,
    ):
        assert name in done
    assert PlayerSortie.objects.filter(assists_air__gt=0).exists()  # level 1 was fixed before the (patched) rebuild
    migrate._run_backfills(cfg())  # pyright: ignore[reportPrivateUsage]
    assert calls == [1]  # marked: not repeated


def test_a_fresh_database_does_not_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = count_rebuilds(monkeypatch)

    migrate._run_backfills(cfg())  # pyright: ignore[reportPrivateUsage]

    assert calls == []
    assert migrate.BACKFILL_ASSIST_SPLIT in SiteSettings.objects.get(pk=1).backfills_done


# --- medal holders ----------------------------------------------------------------------------------------------------


def test_the_holder_counts_come_from_one_aggregate_query() -> None:
    save(mission(tuple(sortie(i, i + 1, kills_air=3, kills_ground=60) for i in range(6))))
    Player.objects.filter(account_uuid=account(1)).update(is_hidden=True)
    expected: dict[tuple[str, int], int] = {}
    for key, tier in PlayerAchievement.objects.filter(player__is_hidden=False, tour=None).values_list("key", "tier"):
        expected[(key, tier)] = expected.get((key, tier), 0) + 1
    assert expected

    with CaptureQueriesContext(connection) as queries:
        recompute_holders()

    assert {(r.key, r.tier): r.holders for r in AchievementHolders.objects.filter(tour=None)} == expected
    # the holder counts of every scope in one query, plus the two pilot counts (all time, per tour)
    assert sum("COUNT(" in q["sql"] for q in queries) == 3


# --- stat thresholds ---------------------------------------------------------------------------------------------


Summary = dict[str, tuple[int, float, float, float]]


def reference(rules: MarkRules, rows: list[tuple[float, ...]], fields: tuple[str, ...], tour: bool) -> Summary:
    pilots = [Totals(**dict(zip(fields, values, strict=True))) for values in rows]  # pyright: ignore[reportArgumentType]
    out: Summary = {}
    for metric in METRICS:
        if tour and metric in ELO_METRICS:
            continue
        found = thresholds(metric_value(metric, t) for t in pilots if amount(metric, t) >= rules.minimum(metric))
        if found is not None:
            out[metric] = (found.population, found.p10, found.p50, found.p90)
    return out


def test_the_prefilter_gives_the_thresholds_of_the_unfiltered_population() -> None:
    rules = MarkRules(min_sorties=2, min_elo_games=2, min_time_on_target_s=500.0, min_air_superiority_s=1000.0)
    save(mission(tuple(sortie(i, i + 1, kills_air=i % 4) for i in range(80))))
    # a world where each pilot reaches a different subset of the minimums (and some none)
    for n, player in enumerate(Player.objects.order_by("pk")):
        player.sorties = n % 6
        player.time_on_target_s = float((n % 4) * 300)
        player.flight_time_air_s = float((n % 5) * 600)
        player.elo_prop_games = n % 3
        player.elo_jet_games = (n + 1) % 4
        player.save()
    tour = Tour.objects.first()
    assert tour is not None
    for n, row in enumerate(PlayerTour.objects.filter(tour=tour).order_by("pk")):
        row.sorties = n % 7
        row.time_on_target_s = float((n % 3) * 400)
        row.flight_time_air_s = float((n % 4) * 700)
        row.save()
    StatThreshold.objects.all().delete()

    recompute_thresholds(rules)

    def stored(tour_id: int | None) -> Summary:
        return {r.metric: (r.population, r.p10, r.p50, r.p90) for r in StatThreshold.objects.filter(tour_id=tour_id)}

    all_time = _FIELDS + _ELO_FIELDS
    assert stored(None) == reference(rules, list(Player.objects.values_list(*all_time)), all_time, tour=False)
    assert stored(tour.pk) == reference(
        rules, list(PlayerTour.objects.filter(tour=tour).values_list(*_FIELDS)), _FIELDS, tour=True
    )
    assert stored(None)  # the world is not empty
