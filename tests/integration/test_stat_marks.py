"""Stat thresholds and marks (FR-WEB-22, doc 14): level-2 percentiles, rebuild == incremental, the profile badges.

September: 25 pilots with one sortie each, pilot n has n - 1 air kills (so p50 = 12, p90 = 21.6) and every fifth pilot
dies. October: pilots 1-22 fly again without kills."""

from datetime import timedelta

import pytest
from django.test import Client

from il2ks.core.stat_marks import METRICS, MarkRules
from il2ks.db.models import Player, StatThreshold, Tour
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.stat_marks import recompute_thresholds
from tests.factories import STARTED_AT, account, meta, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

ONE = MarkRules(min_sorties=1)
TWO = MarkRules(min_sorties=2)


def seed(marks: MarkRules = ONE) -> None:
    september = tuple(
        sortie(
            i,
            i + 1,
            kills_air=i,
            is_death=(i + 1) % 5 == 0,
            is_plane_lost=(i + 1) % 5 == 0,
            outcome="shot_down" if (i + 1) % 5 == 0 else "landed",
        )
        for i in range(25)
    )
    save(mission(september), meta("2026-09-19_22-34-13", STARTED_AT), marks=marks)
    october = tuple(sortie(i, i + 1) for i in range(22))
    save(mission(october), meta("2026-10-02_10-00-00", STARTED_AT + timedelta(days=13)), marks=marks)


def pk(n: int) -> int:
    return Player.objects.get(account_uuid=account(n)).pk


def tour(title: str) -> Tour:
    return Tour.objects.get(title=title)


def state() -> list[tuple[int | None, str, int, int, tuple[float, ...]]]:
    """The thresholds without PKs, in a fixed order."""
    return [
        (r.tour_id, r.metric, r.min_sorties, r.population, (r.p10, r.p25, r.p50, r.p75, r.p90))
        for r in StatThreshold.objects.order_by("tour_id", "metric")
    ]


def test_thresholds_exist_all_time_and_per_tour() -> None:
    seed()
    assert {r.tour_id for r in StatThreshold.objects.all()} == {
        None,
        tour("September 2026").pk,
        tour("October 2026").pk,
    }
    september = StatThreshold.objects.get(tour=tour("September 2026"), metric="air_per_sortie")
    assert (september.population, september.p50, september.p90) == (25, 12.0, pytest.approx(21.6))
    assert september.min_sorties == 1
    # K/D needs a death: only every fifth pilot has one, so the population is below the minimum and K/D has no row
    assert not StatThreshold.objects.filter(tour=tour("September 2026"), metric="kd").exists()
    assert {r.metric for r in StatThreshold.objects.filter(tour=tour("September 2026"))} <= set(METRICS)


def test_rebuild_equals_incremental() -> None:
    seed()
    incremental = state()
    assert incremental
    rebuild_aggregates(marks=ONE)
    assert state() == incremental
    StatThreshold.objects.all().delete()
    recompute_thresholds(ONE)
    assert state() == incremental


def test_hidden_players_count_toward_the_population() -> None:
    seed()
    Player.objects.filter(account_uuid=account(25)).update(is_hidden=True)
    rebuild_aggregates(marks=ONE)
    assert StatThreshold.objects.get(tour=tour("September 2026"), metric="air_per_sortie").population == 25


def test_a_scope_that_shrinks_below_the_population_loses_its_rows() -> None:
    seed()
    assert StatThreshold.objects.filter(tour=tour("October 2026")).exists()
    rebuild_aggregates(marks=MarkRules(min_sorties=2))  # nobody has two sorties in October
    assert not StatThreshold.objects.filter(tour=tour("October 2026")).exists()
    assert StatThreshold.objects.filter(tour=None).exists()  # 22 pilots with two sorties all-time


def test_profile_marks_the_best_pilots_of_the_selected_tour(client: Client) -> None:
    seed()
    september = f"?tour={tour('September 2026').pk}"
    best = client.get(f"/players/{pk(25)}/{september}").content.decode()  # 24 air kills in one sortie
    assert "stat-mark--top" in best
    assert "Top 10%" in best
    assert "Better than 9 in 10 pilots with at least 1 sortie" in best
    middle = client.get(f"/players/{pk(13)}/{september}").content.decode()  # 12 kills: the median
    assert "stat-mark" not in middle.replace("stat-mark-note", "")
    lowest = client.get(f"/players/{pk(1)}/{september}").content.decode()  # 0 kills: never marked
    assert "stat-mark--" not in lowest


def test_marks_follow_the_selected_tour(client: Client) -> None:
    seed()
    # In October nobody has a kill: the best September pilot has no mark there, and October has no kill thresholds
    october = client.get(f"/players/{pk(5)}/?tour={tour('October 2026').pk}").content.decode()
    assert "stat-mark--" not in october


def test_pilots_under_the_minimum_get_a_note_instead_of_marks(client: Client) -> None:
    seed(TWO)
    page = client.get(f"/players/{pk(24)}/").content.decode()  # one sortie all-time
    assert "from 2 sorties on" in page
    assert "stat-mark--" not in page
    flown_twice = client.get(f"/players/{pk(1)}/").content.decode()
    assert "from 2 sorties on" not in flown_twice


def test_profile_query_budget_with_marks(client: Client) -> None:
    """One extra read for the thresholds, all-time and per tour (TD-22: a simple SELECT)."""
    seed()
    assert_simple_reads(client, f"/players/{pk(25)}/", max_queries=11)  # as test_player_pages (+ streak, killboard)
    assert_simple_reads(
        client, f"/players/{pk(25)}/?tour={tour('September 2026').pk}", max_queries=12
    )  # + the PlayerTour row
