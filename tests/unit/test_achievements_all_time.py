"""All-time tiers of the cumulative medals (x5 the per-tour thresholds) and the tours-in-a-row medal (FR-WEB-26, doc 17,
OQ-128, maintainer 2026-10-05). Pure functions, no database."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from il2ks.core.achievement_rules import Rules
from il2ks.core.achievements import (
    ACHIEVEMENTS,
    ALL_TIME_FACTOR,
    BY_KEY,
    AchievementSortie,
    consecutive_tours,
    earn,
    earn_all,
    earn_tours,
)

T0 = datetime(2026, 9, 7, 20, 0, tzinfo=UTC)


def s(n: int = 0, *, air: int = 0) -> AchievementSortie:
    start = T0 + timedelta(minutes=n)
    return AchievementSortie(
        sortie_id=100 + n,
        mission_id=10 + n,
        spawned_at=start,
        ended_at=start + timedelta(minutes=30),
        aircraft_id=1,
        flight_time_s=1800.0,
        kills_air=air,
        kills_ground=0,
        kills_ground_tank=0,
        kills_strike_air=0,
        landing_damage=0.0,
        landed=True,
        is_death=False,
        is_captured=False,
        not_taken_off=False,
    )


def test_a_cumulative_medal_has_five_times_the_thresholds_all_time_and_the_others_have_the_same() -> None:
    assert ALL_TIME_FACTOR == 5
    career = BY_KEY["career_kills"]
    assert career.thresholds == (1, 10, 50, 250)  # per tour: today's tiers
    assert career.all_time_thresholds == (5, 50, 250, 1250)
    assert BY_KEY["flight_hours"].all_time_thresholds == (5, 50, 250, 1000)
    assert BY_KEY["life_kills"].all_time_thresholds == BY_KEY["life_kills"].thresholds  # a max over tours: the same
    for a in ACHIEVEMENTS:
        expected = tuple(ALL_TIME_FACTOR * t for t in a.thresholds) if a.cumulative else a.thresholds
        assert a.all_time_thresholds == expected, a.key


def test_admin_changed_per_tour_thresholds_scale_into_the_all_time_ones() -> None:
    rules = Rules(thresholds={"career_kills": (2, 20, 100, 500), "life_kills": (6, 11, 21, 51)})
    career = rules.achievement("career_kills")
    assert career is not None
    assert career.thresholds == (2, 20, 100, 500)
    assert career.all_time_thresholds == (10, 100, 500, 2500)
    life = rules.achievement("life_kills")
    assert life is not None
    assert life.all_time_thresholds == (6, 11, 21, 51)


def test_earn_all_time_uses_the_scaled_thresholds_and_a_tour_the_base_ones() -> None:
    career = BY_KEY["career_kills"]
    rows = [s(0, air=6), s(1, air=6), s(2, air=40)]  # 6, 12, 52 kills

    assert [(e.tier, e.index) for e in earn(career, rows)] == [(1, 0), (2, 1), (3, 2)]  # per tour: 1, 10, 50
    # all time: 5 (6 kills at index 0), 50 (52 at index 2), 250 never
    assert [(e.tier, e.index) for e in earn(career, rows, all_time=True)] == [(1, 0), (2, 2)]
    # with 40 kills of earlier tours carried in: 46, 52, 92: tier 1 at index 0, tier 2 (50) at index 1
    assert [(e.tier, e.index) for e in earn(career, rows, carried_in=40.0, all_time=True)] == [(1, 0), (2, 1)]


def test_consecutive_tours_a_gap_resets_and_the_best_run_never_falls() -> None:
    assert consecutive_tours([True, True, False, True, True, True]) == [1, 2, 2, 2, 2, 3]
    assert consecutive_tours([False, False]) == [0, 0]
    assert consecutive_tours([]) == []


def test_tours_in_a_row_is_an_all_time_only_achievement_with_tiers_two_three_six_twelve() -> None:
    a = BY_KEY["tours_in_a_row"]
    assert a.all_time_only
    assert a.thresholds == (2, 3, 6, 12)
    assert a.all_time_thresholds == a.thresholds
    assert not a.cumulative
    # a run of 3, a gap, then a run of 6 that reaches tier 3 (6) at the last tour; tier 4 (12) never
    played = [True, True, True, False, True, True, True, True, True, True]
    assert [(e.tier, e.index) for e in earn_tours(a, played)] == [(1, 1), (2, 2), (3, 9)]
    assert earn_tours(a, [True]) == []


def test_the_per_tour_rules_never_earn_an_all_time_only_achievement() -> None:
    assert {a.key for a in ACHIEVEMENTS if a.all_time_only} == {"tours_in_a_row"}
    assert earn_all([s(0, air=3)], achievements=[BY_KEY["tours_in_a_row"]]) == []
    assert earn(BY_KEY["tours_in_a_row"], [replace(s(0), kills_air=9)]) == []
