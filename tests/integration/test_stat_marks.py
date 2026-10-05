"""Stat thresholds and marks (FR-WEB-22, doc 14): level-2 percentiles, rebuild == incremental, the profile badges.

September: 25 pilots with one sortie each, pilot n has n - 1 air kills (so p50 = 12, p90 = 21.6) and every fifth pilot
dies. October: pilots 1-22 fly again without kills."""

import re
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path

import pytest
from django.test import Client

from il2ks.core.ratings.elo import RatingRules
from il2ks.core.stat_marks import METRICS, MarkRules
from il2ks.db.models import Player, PlayerTour, PlayerTourPool, SortieThreshold, StatThreshold, Tour
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

    def ratings(
        rules: RatingRules, tour_ids: Iterable[int] | None = None, *, payload_elo: bool = True, all_time: bool = True
    ) -> int:
        order.append("ratings")
        return real_ratings(rules, tour_ids, payload_elo=payload_elo, all_time=all_time)

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
    assert "stat-mark--top1" in best  # above the p99 (23.76) of the tour
    assert "Top 1%" in best
    assert "Better than 99 in 100. Compared with pilots who have at least 1 sortie." in best
    tenth = client.get(f"/players/{pk(23)}/{september}").content.decode()  # 22 kills: above p90 (21.6), below p95
    assert "stat-mark--top10" in tenth
    assert "Better than 9 in 10." in tenth
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


RULES = MarkRules(min_sorties=1, min_elo_games=3, min_time_on_target_s=600.0, min_attack_sorties=5)


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
            attack_sorties=5,
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
        assert "Compared with pilots who have at least 3 encounters in this pool." in best, query
        low = client.get(f"/players/{pk(2)}/{query}").content.decode()  # below the Elo minimum: no Elo mark
        assert "encounters in this pool" not in low, query


def test_score_hour_mark_text_names_the_minutes(client: Client) -> None:
    seed_scores_and_ratings()
    StatThreshold.objects.filter(tour=None, metric="ground_score_hour").update(
        p75=100.0, p90=150.0, p95=160.0, p99=170.0
    )
    page = client.get(f"/players/{pk(25)}/?tour=all").content.decode()
    assert "Better than 99 in 100. Compared with pilots who have at least 10 minutes on target." in page


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


# --- Top 5% / Top 1% cuts and the sortie page's marks (2026-10-05) ---------------------------------------------------
def sortie_state() -> list[tuple[int | None, str, int, dict[str, int], tuple[float, ...]]]:
    """The sortie populations without PKs, in a fixed order."""
    return [
        (r.tour_id, r.metric, r.population, r.histogram, (r.p10, r.p25, r.p50, r.p75, r.p90, r.p95, r.p99))
        for r in SortieThreshold.objects.order_by("tour_id", "metric")
    ]


def test_player_thresholds_carry_the_top_five_and_top_one_percent_cuts() -> None:
    """September: pilot n has n - 1 air kills, 25 pilots: the type-7 cuts of 0..24 are 21.6, 22.8 and 23.76."""
    seed()
    row = StatThreshold.objects.get(tour=tour("September 2026"), metric="air_per_sortie")
    assert (row.p90, row.p95, row.p99) == (pytest.approx(21.6), pytest.approx(22.8), pytest.approx(23.76))
    assert row.p90 <= row.p95 <= row.p99
    all_time = StatThreshold.objects.get(tour=None, metric="air_per_sortie")
    assert all_time.p90 <= all_time.p95 <= all_time.p99


def test_sortie_populations_exist_per_tour_and_all_time_from_histograms() -> None:
    seed()
    september = SortieThreshold.objects.get(tour=tour("September 2026"), metric="air_kills")
    assert september.population == 25
    assert september.histogram == {str(kills): 1 for kills in range(25)}
    assert (september.p75, september.p90, september.p95, september.p99) == (
        pytest.approx(18.0),
        pytest.approx(21.6),
        pytest.approx(22.8),
        pytest.approx(23.76),
    )
    october = SortieThreshold.objects.get(tour=tour("October 2026"), metric="air_kills")
    assert (october.population, october.histogram) == (22, {"0": 22})  # all zeros: the cuts are 0
    everything = SortieThreshold.objects.get(tour=None, metric="air_kills")
    assert everything.population == 47  # the tours' histograms added up
    assert everything.histogram["0"] == 23
    assert everything.histogram["24"] == 1
    assert SortieThreshold.objects.get(tour=None, metric="ground_kills").histogram == {"0": 47}


def test_sortie_populations_rebuild_equals_incremental() -> None:
    seed()
    incremental = sortie_state()
    assert incremental
    rebuild_aggregates(marks=ONE)
    assert sortie_state() == incremental
    StatThreshold.objects.all().delete()
    SortieThreshold.objects.all().delete()
    recompute_thresholds(ONE)
    assert sortie_state() == incremental
    assert state()  # and the pilots' thresholds, with the new cuts, came back too


def test_a_hidden_pilots_sorties_count_toward_the_sortie_population() -> None:
    seed()
    Player.objects.filter(account_uuid=account(25)).update(is_hidden=True)
    rebuild_aggregates(marks=ONE)
    assert SortieThreshold.objects.get(tour=tour("September 2026"), metric="air_kills").population == 25


def test_a_new_mission_changes_its_tour_and_all_time_but_not_the_other_tour() -> None:
    seed()
    before = SortieThreshold.objects.get(tour=tour("September 2026"), metric="air_kills").histogram
    extra = tuple(sortie(i, 30 + i, kills_air=1) for i in range(3))
    save(mission(extra), meta("2026-10-03_10-00-00", STARTED_AT + timedelta(days=14)), marks=ONE)
    assert SortieThreshold.objects.get(tour=tour("September 2026"), metric="air_kills").histogram == before
    october = SortieThreshold.objects.get(tour=tour("October 2026"), metric="air_kills")
    assert october.histogram == {"0": 22, "1": 3}
    assert SortieThreshold.objects.get(tour=None, metric="air_kills").population == 50


def test_upgrading_a_database_without_the_new_cuts_recomputes_the_thresholds(tmp_path: Path) -> None:
    """`top_tiers` backfill: rows from before the update have p95 / p99 = 0 and no sortie populations; one threshold
    recompute (no level-2 rebuild) fills them, and later migrations do not repeat it."""
    from il2ks.db.site import get_site_settings
    from il2ks.ops import migrate
    from tests.ops_helpers import make_instance

    seed()
    expected, expected_sorties = state(), sortie_state()
    StatThreshold.objects.update(p95=0.0, p99=0.0)
    SortieThreshold.objects.all().delete()

    migrate._run_backfills(make_instance(tmp_path, extra_toml=MARKS_TOML), [migrate.BACKFILL_TOP_TIERS])  # pyright: ignore[reportPrivateUsage]

    assert state() == expected
    assert sortie_state() == expected_sorties
    assert "top_tiers" in get_site_settings().backfills_done


MARKS_TOML = "[marks]\nmin_sorties = 1\n"  # the upgrade reads the configured minimum: the tests' ONE


def sortie_url(number: int, *, tour_name: str = "September 2026") -> str:
    from il2ks.db.models import PlayerSortie

    found = PlayerSortie.objects.get(player_id=pk(number), mission__tour=tour(tour_name))
    return f"/sorties/{found.pk}/"


def test_sortie_page_marks_its_air_kills_against_all_sorties(client: Client) -> None:
    seed()
    SortieThreshold.objects.filter(tour=None).delete()  # the tour's tiers alone (all time: the next test)
    best = client.get(sortie_url(25)).content.decode()  # 24 air kills: above p99 of the tour
    assert "stat-mark--top1" in best
    assert ">Top 1%<" in best
    assert "Compared with all counted pilot sorties of this tour." in best
    fifth = client.get(sortie_url(24)).content.decode()  # 23 kills: above p95 (22.8), not above p99 (23.76)
    assert "stat-mark--top5" in fifth
    tenth = client.get(sortie_url(23)).content.decode()  # 22 kills: above p90 (21.6), not above p95
    assert "stat-mark--top10" in tenth
    quarter = client.get(sortie_url(21)).content.decode()  # 20 kills: above p75 (18), not above p90
    assert "stat-mark--top25" in quarter


def test_sortie_page_uses_the_all_time_tier_when_it_is_better(client: Client) -> None:
    seed()
    # 22 kills: the tour's p95 is 22.8 (top 10%), but all time October's zero-kill sorties lower the cuts: p95 = 21.7
    html = client.get(sortie_url(23)).content.decode()
    assert "stat-mark--top5" in html
    assert "Compared with all counted pilot sorties of all time." in html


def test_sortie_page_has_no_mark_for_the_middle_zero_or_other_numbers(client: Client) -> None:
    seed()
    for number in (13, 1):  # 12 kills (the median) and none: never marked
        html = client.get(sortie_url(number)).content.decode()
        assert "stat-mark--" not in html
    best = client.get(sortie_url(25)).content.decode()
    assert best.count("stat-mark--") == 1  # the air kills only: no ground kills, no mark for them


def test_sortie_page_marks_ground_kills_too(client: Client) -> None:
    from il2ks.db.models import PlayerSortie

    seed()
    PlayerSortie.objects.filter(player_id=pk(25), mission__tour=tour("September 2026")).update(kills_ground=3)
    recompute_thresholds(ONE)
    html = client.get(sortie_url(25)).content.decode()
    assert html.count("stat-mark--top1") == 2  # air and ground


def test_sortie_page_all_time_mark_when_the_tour_is_too_small(client: Client) -> None:
    """October has 22 sorties (enough); with a minimum population of 20 only the all-time one counts for a tour that
    lost sorties: the all-time population still marks it."""
    seed()
    SortieThreshold.objects.filter(tour=tour("September 2026")).delete()  # as if that tour had too few sorties
    html = client.get(sortie_url(25)).content.decode()
    assert "stat-mark--top1" in html
    assert "Compared with all counted pilot sorties of all time." in html


def test_sortie_page_without_populations_or_for_a_gunner_has_no_marks(client: Client) -> None:
    seed()
    SortieThreshold.objects.all().delete()
    assert "stat-mark--" not in client.get(sortie_url(25)).content.decode()


def test_sortie_page_marks_stay_within_the_read_budget(client: Client) -> None:
    seed()
    # context processor 2, sortie, kills made, kills suffered, counterparts, game objects, medals, their rarity, and the
    # sortie populations: the one read the marks add (only for a pilot sortie with kills)
    assert_simple_reads(client, sortie_url(25), max_queries=2 + 8)
    assert_simple_reads(client, sortie_url(1), max_queries=2 + 7)  # no kills: no population read


# --- the star metrics on the profile: Elo (jet, prop) and attack proficiency, per tour and all time (2026-10-05) -----
def seed_attack_hours() -> None:
    """`seed_scores_and_ratings` plus the same attack figures on the pilots' September PlayerTour rows."""
    seed_scores_and_ratings()
    september = tour("September 2026")
    for n in range(1, 26):
        PlayerTour.objects.filter(player__account_uuid=account(n), tour=september).update(
            time_on_target_s=n * 100.0, score_ground_attack=n * 5.0 * n, attack_sorties=5
        )
    recompute_thresholds(RULES)


def star_tile(page: str, key: str) -> str:
    """The markup of one star-metric tile of the profile (`data-star` = elo-jet, elo-prop or attack)."""
    found = re.search(rf'data-star="{key}".*?\n</div>', page, re.DOTALL)
    assert found, f"no {key} tile"
    return found.group(0)


def scope_query(scope: str) -> str:
    return "?tour=all" if scope == "all" else f"?tour={tour('September 2026').pk}"


@pytest.mark.parametrize("scope", ["all", "tour"])
def test_attack_proficiency_mark_shows_on_the_profile(client: Client, scope: str) -> None:
    """Maintainer 2026-10-05: the attack proficiency (`ground_score_hour`) had no mark on the profile."""
    seed_attack_hours()
    best = star_tile(client.get(f"/players/{pk(25)}/{scope_query(scope)}").content.decode(), "attack")
    assert "stat-mark--top1" in best, scope
    low = star_tile(client.get(f"/players/{pk(2)}/{scope_query(scope)}").content.decode(), "attack")  # < 10 min
    assert "stat-mark--" not in low
    assert "time on target" in low


@pytest.mark.parametrize("scope", ["all", "tour"])
@pytest.mark.parametrize("pool", ["jet", "prop"])
def test_elo_marks_show_all_four_tiers(client: Client, scope: str, pool: str) -> None:
    seed_scores_and_ratings()
    cuts = {"p10": 1000.0, "p25": 1100.0, "p50": 1200.0, "p75": 1300.0, "p90": 1400.0, "p95": 1500.0, "p99": 1600.0}
    for tour_id in (None, tour("September 2026").pk):  # the pools are tiny in the seed: fix the cuts
        StatThreshold.objects.update_or_create(
            tour_id=tour_id, metric=f"elo_{pool}", defaults={"min_sorties": 3, "population": 20, **cuts}
        )
    player = Player.objects.get(account_uuid=account(25))
    for rating, band in ((1650.0, "top1"), (1550.0, "top5"), (1450.0, "top10"), (1350.0, "top25"), (1250.0, None)):
        Player.objects.filter(pk=player.pk).update(**{f"elo_{pool}": rating, f"elo_{pool}_games": 5})
        PlayerTourPool.objects.filter(player=player, propulsion=pool).update(elo=rating, elo_games=5)
        page = client.get(f"/players/{player.pk}/{scope_query(scope)}").content.decode()
        marks = re.findall(r"stat-mark--(top\d+)", star_tile(page, f"elo-{pool}"))
        assert marks == ([band] if band else []), (scope, pool, rating, marks)


def test_attack_proficiency_population_needs_the_boards_attack_sorties() -> None:
    """Root cause of the missing attack proficiency marks (2026-10-05): pilots with one lucky attack sortie of 10
    minutes were in the population (the board needs `min_attack_sorties` too), their huge rates pushed every tier
    out of reach."""
    seed_scores_and_ratings()
    Player.objects.filter(account_uuid=account(1)).update(
        attack_sorties=1,
        time_on_target_s=900.0,
        score_ground_attack=9000.0,  # 36 000 per hour
    )
    recompute_thresholds(RULES)
    hour = StatThreshold.objects.get(tour=None, metric="ground_score_hour")
    assert (hour.population, hour.p99) == (20, pytest.approx(180.0 * 24.81))  # not 21, and not 36 000
