"""Stat thresholds and marks (FR-WEB-22, doc 14): level-2 percentiles, rebuild == incremental, the profile badges.

September: 25 pilots with one sortie each, pilot n has n - 1 air kills (so p50 = 12, p90 = 21.6) and every fifth pilot
dies. October: pilots 1-22 fly again without kills."""

from collections.abc import Iterable
from datetime import timedelta

import pytest
from django.test import Client

from il2ks.core.ratings.elo import RatingRules
from il2ks.core.stat_marks import METRICS, MarkRules
from il2ks.db.models import Player, PlayerTourPool, StatThreshold, Tour
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.stat_marks import recompute_thresholds
from tests.factories import STARTED_AT, account, meta, mission, save, sortie
from tests.simple_reads import PROFILE_READS_ALL_TIME, PROFILE_READS_TOUR, assert_simple_reads

pytestmark = pytest.mark.django_db

ONE = MarkRules(min_sorties=1)
TWO = MarkRules(min_sorties=2)


def test_thresholds_are_computed_after_the_elo_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    """QA 2026-10-04: the Elo percentiles were taken before the mission's ratings were written and lagged one mission
    behind a rebuild (the real sample's elo_jet p50 differed)."""
    from il2ks.ingest import aggregates, persist

    order: list[str] = []
    real_ratings, real_marks = aggregates.recompute_ratings, persist.recompute_thresholds

    def ratings(rules: RatingRules, tour_ids: Iterable[int] | None = None, *, payload_elo: bool = True) -> int:
        order.append("ratings")
        return real_ratings(rules, tour_ids, payload_elo=payload_elo)

    def marks(rules: MarkRules, tour_ids: Iterable[int] | None = None) -> None:
        order.append("marks")
        real_marks(rules, tour_ids)

    monkeypatch.setattr(aggregates, "recompute_ratings", ratings)
    monkeypatch.setattr(persist, "recompute_thresholds", marks)

    save(mission((sortie(0, 1),)), meta("2026-09-19_22-34-13", STARTED_AT), marks=ONE)

    assert order == ["ratings", "marks"]


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
    page = client.get(f"/players/{pk(24)}/?tour=all").content.decode()  # one sortie all-time
    assert "from 2 sorties on" in page
    assert "stat-mark--" not in page
    flown_twice = client.get(f"/players/{pk(1)}/?tour=all").content.decode()
    assert "from 2 sorties on" not in flown_twice


RULES = MarkRules(min_sorties=1, min_elo_games=3, min_time_on_target_s=600.0)


def seed_scores_and_ratings() -> None:
    """Pilot n (1..25): 10 n air score, Elo 1400 + 10 n with n // 2 jet games (3+ from pilot 6) and n // 8 prop games,
    n * 100 s on target (10 minutes from pilot 6) with 5 n * n attack score."""
    seed()
    for n in range(1, 26):
        Player.objects.filter(account_uuid=account(n)).update(
            score_air=n * 10.0,
            score_ground=n * 2.0,
            elo_jet=1400.0 + 10 * n,
            elo_jet_games=n // 2,  # 3+ games from pilot 6: 20 pilots, the minimum population
            elo_prop=1400.0 + 10 * n,
            elo_prop_games=n // 8,  # 3+ games from pilot 24: two pilots, too few for a distribution
            time_on_target_s=n * 100.0,
            score_ground_attack=n * 5.0 * n,
        )
    september = tour("September 2026")
    for n in range(1, 26):  # the tour's own pool rows hold the same ratings (a single tour, so best == final)
        for propulsion, games in (("jet", n // 2), ("prop", n // 8)):
            PlayerTourPool.objects.update_or_create(
                player=Player.objects.get(account_uuid=account(n)),
                tour=september,
                propulsion=propulsion,
                defaults={"elo": 1400.0 + 10 * n, "elo_games": games},
            )
    recompute_thresholds(RULES)


def test_score_elo_and_ground_hour_populations() -> None:
    seed_scores_and_ratings()
    alltime = {r.metric: r for r in StatThreshold.objects.filter(tour=None)}
    assert alltime["air_score"].population == 25
    assert alltime["air_score"].min_sorties == 1
    jet = alltime["elo_jet"]  # rated games, whatever the sorties: pilots 6..25
    assert (jet.population, jet.min_sorties) == (20, 3)
    assert jet.p50 == pytest.approx(1400 + 10 * 15.5)
    assert "elo_prop" not in alltime  # two pilots with enough rated games: too few for a distribution
    hour = alltime["ground_score_hour"]  # 600 s on target or more: pilots 6..25
    assert (hour.population, hour.min_sorties) == (20, 600)
    assert hour.p50 == pytest.approx(180.0 * 15.5)  # pilot n: 5 n * n score over n * 100 s = 180 n per hour


def test_elo_thresholds_per_tour_come_from_the_tours_pool_rows() -> None:
    """OQ-128: a tour's Elo marks compare with that tour's ratings, all time with the best-tour ratings."""
    seed_scores_and_ratings()
    september = {r.metric: r for r in StatThreshold.objects.filter(tour=tour("September 2026"))}
    assert (september["elo_jet"].population, september["elo_jet"].min_sorties) == (20, 3)
    assert september["elo_jet"].p50 == pytest.approx(1400 + 10 * 15.5)
    assert "elo_prop" not in september  # two pilots with enough rated games: too few for a distribution
    assert september["air_score"]  # PlayerTour carries the scores (zero here, still defined)
    recompute_thresholds(RULES)  # again: the same rows, no churn
    assert StatThreshold.objects.filter(metric="elo_jet").count() == 2  # all time and the tour


def test_elo_mark_renders_on_all_time_and_tour_profiles(client: Client) -> None:
    seed_scores_and_ratings()
    for query in ("?tour=all", f"?tour={tour('September 2026').pk}"):
        best = client.get(f"/players/{pk(25)}/{query}").content.decode()
        assert "Better than 9 in 10 pilots with at least 3 encounters in this pool" in best, query
        low = client.get(f"/players/{pk(2)}/{query}").content.decode()  # below the Elo minimum: no Elo mark
        assert "encounters in this pool" not in low, query


def test_score_hour_mark_text_names_the_minutes(client: Client) -> None:
    seed_scores_and_ratings()
    StatThreshold.objects.filter(tour=None, metric="ground_score_hour").update(p75=100.0, p90=150.0)
    page = client.get(f"/players/{pk(25)}/?tour=all").content.decode()
    assert "Better than 9 in 10 pilots with at least 10 minutes on target" in page


def test_profile_query_budget_with_score_marks(client: Client) -> None:
    seed_scores_and_ratings()
    assert_simple_reads(client, f"/players/{pk(25)}/", max_queries=PROFILE_READS_TOUR)
    assert_simple_reads(client, f"/players/{pk(25)}/?tour=all", max_queries=PROFILE_READS_ALL_TIME)
    assert_simple_reads(client, f"/players/{pk(25)}/?tour={tour('September 2026').pk}", max_queries=PROFILE_READS_TOUR)


def test_profile_query_budget_with_marks(client: Client) -> None:
    """One extra read for the thresholds, all-time and per tour (TD-22: a simple SELECT)."""
    seed()
    assert_simple_reads(client, f"/players/{pk(25)}/", max_queries=PROFILE_READS_TOUR)
    assert_simple_reads(client, f"/players/{pk(25)}/?tour=all", max_queries=PROFILE_READS_ALL_TIME)
    assert_simple_reads(client, f"/players/{pk(25)}/?tour={tour('September 2026').pk}", max_queries=PROFILE_READS_TOUR)
