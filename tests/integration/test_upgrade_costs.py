"""Costs that must not grow with history: the per-mission medal holder counts and the per-mission stat thresholds
(pre-filtered rows)."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from il2ks.core.stat_marks import ELO_METRICS, METRICS, MarkRules, Totals, metric_value, thresholds
from il2ks.db.models import (
    AchievementHolders,
    Player,
    PlayerAchievement,
    PlayerTour,
    StatThreshold,
    Tour,
)
from il2ks.ingest.achievements import recompute_holders
from il2ks.ingest.stat_marks import _ELO_FIELDS, _FIELDS, recompute_thresholds  # pyright: ignore[reportPrivateUsage]
from tests.factories import account, mission, save, sortie

pytestmark = pytest.mark.django_db

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
        found = thresholds(metric_value(metric, t) for t in pilots if rules.qualifies(metric, t))
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
        player.attack_sorties = n % 7  # attack proficiency also needs attack sorties
        player.save()
    tour = Tour.objects.first()
    assert tour is not None
    for n, row in enumerate(PlayerTour.objects.filter(tour=tour).order_by("pk")):
        row.sorties = n % 7
        row.time_on_target_s = float((n % 3) * 400)
        row.flight_time_air_s = float((n % 4) * 700)
        row.attack_sorties = n % 8
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
