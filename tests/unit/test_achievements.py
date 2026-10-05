"""Achievement rules (FR-WEB-26, doc 17): pure functions over a pilot's sorties, no database."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from il2ks.core.achievements import (
    ACHIEVEMENTS,
    BY_KEY,
    Achievement,
    AchievementSortie,
    consecutive_weeks,
    earn,
    earn_all,
    life_kills,
    survived_in_a_row,
    type_veteran,
    week_number,
)

T0 = datetime(2026, 9, 7, 20, 0, tzinfo=UTC)  # a Monday


def s(
    n: int = 0,
    *,
    day: float = 0.0,
    aircraft: int = 1,
    flight_h: float = 0.5,
    air: int = 0,
    ground: int = 0,
    tanks: int = 0,
    strike: int = 0,
    damage: float = 0.0,
    landed: bool = True,
    death: bool = False,
    captured: bool = False,
    grounded: bool = False,
) -> AchievementSortie:
    start = T0 + timedelta(days=day)
    return AchievementSortie(
        sortie_id=100 + n,
        mission_id=10 + n,
        spawned_at=start,
        ended_at=start + timedelta(hours=flight_h),
        aircraft_id=aircraft,
        flight_time_s=flight_h * 3600,
        kills_air=air,
        kills_ground=ground,
        kills_ground_tank=tanks,
        kills_strike_air=strike,
        damage_taken=damage,
        landed=landed and not death and not grounded,
        is_death=death,
        is_captured=captured,
        not_taken_off=grounded,
    )


def run(rows: list[AchievementSortie]) -> list[AchievementSortie]:
    """Number the sorties in order, one per hour of day offset so spawn times stay chronological."""
    return [replace(r, sortie_id=100 + n, mission_id=10 + n) for n, r in enumerate(rows)]


def tiers(key: str, rows: list[AchievementSortie]) -> dict[int, int]:
    """tier -> index of the sortie that first reached it."""
    return {e.tier: e.index for e in earn(BY_KEY[key], rows)}


def test_registry_is_consistent() -> None:
    assert len({a.key for a in ACHIEVEMENTS}) == len(ACHIEVEMENTS)
    for a in ACHIEVEMENTS:
        assert list(a.thresholds) == sorted(set(a.thresholds)), a.key
        assert 3 <= a.top_tier <= 4
    assert earn_all([]) == []


def test_no_sorties_earn_nothing() -> None:
    for a in ACHIEVEMENTS:
        assert earn(a, []) == []


def test_life_kills_accumulate_over_sorties_and_reset_on_death() -> None:
    rows = [s(air=3), s(air=2), s(air=4, death=True), s(air=3), s(air=3)]

    # 3, 5 (tier 1 at index 1), 9 (the fatal sortie counts), then a new life: 3, 6
    assert tiers("life_kills", rows) == {1: 1}
    assert life_kills(rows) == [3.0, 5.0, 9.0, 9.0, 9.0]  # the running best never falls
    assert tiers("life_kills", [*rows, s(air=1, death=False)]) == {1: 1}


def test_life_kills_second_tier_and_capture_ends_a_life() -> None:
    rows = [s(air=5), s(air=5), s(air=1, captured=True), s(air=9), s(air=9)]

    assert tiers("life_kills", rows) == {1: 0, 2: 1}  # 5, 10, then reset; 9 + 9 = 18 in the new life
    assert tiers("life_kills", [*rows, s(air=1)]) == {1: 0, 2: 1}  # 19 in the new life: one short of tier 3


def test_life_kills_skip_grounded_sorties() -> None:
    rows = [s(air=4), s(grounded=True), s(air=1)]

    assert life_kills(rows) == [4.0, 4.0, 5.0]  # a spawn that never took off neither adds nor ends the life


def test_survived_in_a_row_matches_the_ironman_rule() -> None:
    rows = [s(), s(), s(grounded=True), s(), s(death=True), s(), s(captured=True), s(), s(), s()]

    assert survived_in_a_row(rows) == [1.0, 2.0, 2.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0]
    five = [s() for _ in range(5)]
    assert tiers("survivor", five) == {1: 4}


def test_disconnect_and_mission_end_do_not_break_a_streak() -> None:
    rows = [replace(s(), landed=False) for _ in range(5)]  # cut off at mission end / disconnected: no death

    assert tiers("survivor", rows) == {1: 4}


def test_sortie_kills_is_the_best_single_sortie() -> None:
    assert tiers("sortie_kills", [s(air=1), s(air=2), s(air=3), s(air=1)]) == {1: 1, 2: 2}
    assert tiers("sortie_kills", [s(air=5, death=True)]) == {1: 0, 2: 0, 3: 0}  # a fatal sortie still counts


def test_career_kills_and_hours_and_sorties_are_cumulative() -> None:
    rows = [s(air=10), s(air=15), s(air=1)]

    assert tiers("career_kills", rows) == {1: 0, 2: 0}
    assert tiers("frequent_flyer", [s() for _ in range(10)]) == {1: 9}
    assert tiers("frequent_flyer", [s() for _ in range(9)] + [s(grounded=True)]) == {}
    assert tiers("flight_hours", [s(flight_h=0.6), s(flight_h=0.6)]) == {1: 1}


def test_strike_hunter_and_tank_buster_sum_their_counters() -> None:
    assert tiers("strike_hunter", [s(strike=1), s(strike=4), s(strike=10)]) == {1: 0, 2: 1, 3: 2}
    assert tiers("tank_buster", [s(tanks=2), s(tanks=1), s(tanks=7)]) == {1: 1, 2: 2}


def test_ground_sortie_is_the_best_single_sortie() -> None:
    assert tiers("ground_sortie", [s(ground=4), s(ground=60), s(ground=3)]) == {1: 1, 2: 1}


def test_damaged_landing_needs_a_kill_a_landing_and_heavy_damage() -> None:
    def hurt() -> AchievementSortie:
        return s(damage=0.7, air=1)

    rows = [
        s(damage=0.9, air=0),  # no kill
        s(damage=0.2, air=2),  # barely scratched
        s(damage=0.8, air=1, landed=False),  # did not land
        s(damage=0.6, ground=3),  # counts: ground kills too
        hurt(),
    ]

    assert tiers("damaged_landing", rows) == {1: 3}
    assert tiers("damaged_landing", [*rows, hurt(), hurt()]) == {1: 3, 2: 5}


def test_weeks_in_a_row_use_iso_weeks_and_a_gap_restarts() -> None:
    weekly = [s(day=7 * w) for w in range(4)]  # Monday of four weeks running

    assert tiers("regular", weekly) == {1: 1, 2: 3}
    gap = [s(day=0), s(day=7), s(day=21), s(day=28)]  # week 3 missing: runs of 2 and 2
    assert consecutive_weeks(gap) == [1.0, 2.0, 2.0, 2.0]
    assert tiers("regular", gap) == {1: 1}


def test_weeks_in_a_row_edges() -> None:
    sunday_monday = [s(day=6.0), s(day=6.25)]  # Sunday 20:00, then Monday 02:00: two different ISO weeks
    assert week_number(sunday_monday[0].spawned_at) + 1 == week_number(sunday_monday[1].spawned_at)
    assert tiers("regular", sunday_monday) == {1: 1}
    assert tiers("regular", [s(day=0), s(day=1), s(day=2)]) == {}  # one week, three days
    assert tiers("regular", [s(day=0), s(day=7, grounded=True)]) == {}  # a spawn that never flew is no week played
    sixteen = [s(day=7 * w) for w in range(16)]
    assert tiers("regular", sixteen)[4] == 15


def test_weeks_in_a_row_across_the_new_year() -> None:
    dec = replace(s(), spawned_at=datetime(2026, 12, 28, 12, tzinfo=UTC))  # ISO week 53 of 2026
    jan = replace(s(), spawned_at=datetime(2027, 1, 4, 12, tzinfo=UTC))  # ISO week 1 of 2027
    assert consecutive_weeks([dec, jan]) == [1.0, 2.0]


def test_type_veteran_counts_one_type_only() -> None:
    mixed = [s(aircraft=1, flight_h=1.0), s(aircraft=2, flight_h=1.0), s(aircraft=1, flight_h=1.0)]

    assert type_veteran(mixed) == [1.0, 1.0, 2.0]
    assert tiers("type_veteran", mixed) == {1: 2}
    assert tiers("type_veteran", [s(aircraft=n % 5, flight_h=0.5) for n in range(9)]) == {}


def test_a_tier_keeps_its_first_sortie_and_is_never_lost() -> None:
    rows = [s(air=3), s(air=2, death=True), s(air=0), s(air=0)]

    earned = earn(BY_KEY["life_kills"], rows)
    assert [(e.tier, e.index) for e in earned] == [(1, 1)]
    more = earn(BY_KEY["life_kills"], [*rows, s(air=1)])
    assert [(e.tier, e.index) for e in more] == [(1, 1)]  # a longer history keeps the earlier tiers and sorties


def test_earn_all_is_the_union_of_the_definitions() -> None:
    rows = run([s(air=6, ground=25) for _ in range(3)])

    keys = {(e.key, e.tier) for e in earn_all(rows)}
    assert ("life_kills", 1) in keys
    assert ("ground_sortie", 1) in keys
    assert ("career_kills", 1) in keys
    assert all(e.index < len(rows) for e in earn_all(rows))


@pytest.mark.parametrize("a", ACHIEVEMENTS, ids=lambda a: a.key)
def test_progress_is_monotone_and_has_one_value_per_sortie(a: Achievement) -> None:
    rows = [s(air=n % 3, ground=n % 7, death=n % 5 == 4, day=n * 3, aircraft=n % 2) for n in range(30)]
    values = a.progress(rows)

    assert len(values) == len(rows)
    assert values == sorted(values)


# --- ribbons and rarity (doc 17, OQ-105) -----------------------------------------------------------------------
def test_definitions_default_to_medals_and_the_simple_ones_are_ribbons() -> None:
    assert Achievement("x", (1,), "count", life_kills).kind == "medal"
    assert not Achievement("x", (1,), "count", life_kills).shame
    ribbons = {a.key for a in ACHIEVEMENTS if a.kind == "ribbon"}
    assert {"frequent_flyer", "regular", "flight_hours", "career_kills"} <= ribbons
    assert BY_KEY["life_kills"].kind == "medal"


def test_rarity_text_and_emphasis_follow_the_share_of_pilots() -> None:
    from il2ks.queries.achievements import Holding
    from il2ks.web.medals import NO_RARITY, rarity

    assert rarity(None) is NO_RARITY
    assert rarity(Holding(3, 0)) is NO_RARITY  # a row from before the pilot counts existed
    assert rarity(Holding(1, 200)).level == "epic"  # 0.5 %
    assert rarity(Holding(1, 200)).text == "Held by 0.5% of pilots"
    assert rarity(Holding(8, 200)).level == "rare"  # 4 %
    assert rarity(Holding(40, 200)).level == ""  # 20 %
    assert rarity(Holding(40, 200)).text == "Held by 20% of pilots"
    assert rarity(Holding(1, 10)).level == ""  # too few pilots to call anything rare
    assert rarity(Holding(1, 5000)).text == "Held by less than 0.1% of pilots"


def test_a_shame_tier_is_never_emphasised_as_rare() -> None:
    """A rare Hard Landing is not a trophy: the text stays, the ring and glow do not (doc 17, QA 2026-10-04)."""
    from il2ks.queries.achievements import Holding
    from il2ks.web.medals import rarity

    assert rarity(Holding(1, 5000), shame=True).level == ""
    assert rarity(Holding(1, 5000), shame=True).text == "Held by less than 0.1% of pilots"
    assert rarity(Holding(1, 5000)).level == "epic"


def test_earn_with_a_carried_in_total_reaches_the_tiers_sooner() -> None:
    """A cumulative medal replayed over one tour with the earlier tours' total (clean slate: all time = the sum)."""
    career = BY_KEY["career_kills"]
    rows = [s(0, air=3), s(1, air=3), s(2, air=3)]

    assert [(e.tier, e.index) for e in earn(career, rows)] == [(1, 0)]  # 9 kills: below the 10 of tier 2
    assert [(e.tier, e.index) for e in earn(career, rows, carried_in=6.0)] == [(1, 0), (2, 1)]  # 6 + 3 + 3 = 12
    assert earn(career, rows, carried_in=0.0) == earn(career, rows)


def test_only_the_pure_running_totals_are_cumulative_and_the_elo_medal_is_per_tour() -> None:
    cumulative = {a.key for a in ACHIEVEMENTS if a.cumulative}
    assert cumulative == {
        "career_kills",
        "tank_buster",
        "flight_hours",
        "shame_taxi",
        "shame_friendly",
        "shame_strafed",
    }
    assert all(a.counter for a in ACHIEVEMENTS if a.cumulative)
    assert "elo_peak" in {e.key for e in earn_all([replace(s(0), elo_peak=1600.0)])}
