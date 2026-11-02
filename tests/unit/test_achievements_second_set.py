"""The second set of achievements (doc 17, OQ-105): Elo, ground score, rams, first blood, multi-kills, types, landing
streak, ace in a day and the hall-of-shame medals. Pure functions, no database."""

import csv
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from il2ks.core import catalog
from il2ks.core.achievements import (
    ACHIEVEMENTS,
    BY_KEY,
    MIN_TIME_ON_TARGET_S,
    Achievement,
    AchievementSortie,
    attack_rate,
    earn,
    ground_score,
    highest_elo,
    kills_in_a_day,
    landings_in_a_row,
    types_flown,
    types_with_kills,
)
from il2ks.core.replay.kills import BURST_WINDOW_S, first_blood_sortie, max_burst
from il2ks.core.replay.result import KillResult

T0 = datetime(2026, 9, 7, 20, 0, tzinfo=UTC)


def s(
    n: int = 0,
    *,
    day: float = 0.0,
    aircraft: int = 1,
    air: int = 0,
    landed: bool = True,
    death: bool = False,
    captured: bool = False,
    grounded: bool = False,
    mission_end: bool = False,
    **facts: object,
) -> AchievementSortie:
    start = T0 + timedelta(days=day, minutes=n)
    return replace(
        AchievementSortie(
            sortie_id=100 + n,
            mission_id=10 + n,
            spawned_at=start,
            ended_at=start + timedelta(minutes=30),
            aircraft_id=aircraft,
            flight_time_s=1800.0,
            kills_air=air,
            kills_ground=0,
            kills_ground_tank=0,
            kills_strike_air=0,
            landing_damage=0.0,
            landed=landed and not death and not grounded,
            is_death=death,
            is_captured=captured,
            not_taken_off=grounded,
            ended_by_mission_end=mission_end,
        ),
        **facts,
    )


def tiers(key: str, rows: list[AchievementSortie]) -> dict[int, int]:
    return {e.tier: e.index for e in earn(BY_KEY[key], rows)}


def test_registry_flags() -> None:
    assert BY_KEY["life_kills"].kind == "medal"
    assert BY_KEY["types_flown"].kind == "ribbon"


def test_elo_is_the_highest_peak_so_far() -> None:
    rows = [s(), s(elo_peak=1512.0), s(elo_peak=1490.0), s(elo_peak=1620.0), s()]

    assert highest_elo(rows) == [0.0, 1512.0, 1512.0, 1620.0, 1620.0]  # a lower later peak never lowers the medal
    assert tiers("elo_peak", rows) == {1: 3, 2: 3, 3: 3}
    assert tiers("elo_peak", [s(), s()]) == {}  # 0 = no rated game; the start rating alone earns nothing


def test_elo_peak_counts_in_a_fatal_sortie() -> None:
    assert tiers("elo_peak", [s(elo_peak=1710.0, death=True)]) == {1: 0, 2: 0, 3: 0, 4: 0}


def test_ground_score_adds_up_and_never_drops() -> None:
    rows = [s(ground_points=80.0), s(ground_points=30.0), s(ground_points=-50.0), s(ground_points=0.0)]

    assert ground_score(rows) == [80.0, 110.0, 110.0, 110.0]
    assert tiers("ground_score", rows) == {1: 1}


def test_first_blood_counts_missions() -> None:
    rows = [s(first_blood=True), s(), s(first_blood=True), s(first_blood=True, death=True)]

    assert tiers("first_blood", rows) == {1: 0, 2: 3}


def test_multi_kill_is_the_best_burst_of_one_sortie() -> None:
    rows = [s(air=2, multi_kill=2), s(air=5, multi_kill=1), s(air=4, multi_kill=3)]

    assert tiers("multi_kill", rows) == {1: 0, 2: 2}
    assert tiers("multi_kill", [s(air=1, multi_kill=1)]) == {}  # a single kill is no multi-kill


def test_max_burst_window() -> None:
    sec = 50  # ticks per second
    window = round(BURST_WINDOW_S)

    assert max_burst([]) == 0
    assert max_burst([100]) == 1
    assert max_burst([0, 10 * sec, 20 * sec]) == 3
    assert max_burst([0, window * sec, 2 * window * sec]) == 2  # the window is inclusive at its edge
    assert max_burst([0, (window + 1) * sec, 2 * (window + 1) * sec]) == 1
    assert max_burst([3 * sec, 0, 4 * sec]) == 3  # unsorted input
    assert max_burst([0, 5 * sec, 500 * sec, 505 * sec, 510 * sec]) == 3  # the best of two bursts


def kill_result(tick: int, killer: int | None, victim: int | None, **over: object) -> KillResult:
    base = KillResult(
        tick=tick,
        victim_object_id=1,  # type: ignore[arg-type]
        victim_type="F-86A-5",
        victim_kind="air",
        victim_coalition=2,
        victim_sortie_index=victim,
        killer_sortie_index=killer,
        killer_type="MiG-15bis",
        credit="kill",
        via="direct",
        is_friendly=False,
        pos=None,
    )
    return replace(base, **over)


def test_first_blood_sortie_picks_the_first_pvp_air_kill() -> None:
    pilots = frozenset({0, 1, 2, 3})
    kills = [
        kill_result(50, 0, None),  # an AI aircraft: not a first blood
        kill_result(100, 0, 3, is_friendly=True),  # friendly fire
        kill_result(120, 1, 3, credit="assist"),  # an assist
        kill_result(130, 9, 3),  # not a pilot sortie (a gunner)
        kill_result(150, 2, 3),
        kill_result(200, 1, 3),
    ]

    assert first_blood_sortie(kills, pilots) == 2
    assert first_blood_sortie(kills[:5], pilots) == 2
    assert first_blood_sortie(kills[:4], pilots) is None
    assert first_blood_sortie([], pilots) is None
    assert first_blood_sortie([kill_result(10, 1, 2, victim_kind="ground")], pilots) is None


def test_first_blood_sortie_tie_goes_to_the_first_listed() -> None:
    assert first_blood_sortie([kill_result(10, 2, 3), kill_result(10, 1, 3)], frozenset({1, 2, 3})) == 2


def test_types_flown_counts_distinct_types_that_took_off() -> None:
    rows = [s(aircraft=1), s(aircraft=1), s(aircraft=2, grounded=True), s(aircraft=3), s(aircraft=2)]

    assert types_flown(rows) == [
        1.0,
        1.0,
        1.0,
        2.0,
        3.0,
    ]  # the grounded spawn in type 2 did not count, the later one did
    assert tiers("types_flown", [s(aircraft=n) for n in range(3)]) == {1: 2}


def test_the_type_medals_top_out_at_the_number_of_playable_aircraft() -> None:
    """Maintainer 2026-10-31: 8 types for now. New aircraft in the catalog: raise the top tiers of these two medals."""
    objects = Path(catalog.__file__).parent / "data" / "objects.csv"
    with objects.open(encoding="utf-8", newline="") as rows:
        aircraft = [r for r in csv.DictReader(rows) if r["is_playable"] == "true" and r["cls"] != "gunner"]

    assert BY_KEY["types_flown"].thresholds[-1] == len(aircraft)
    assert BY_KEY["types_with_kills"].thresholds[-1] == len(aircraft)


def test_types_with_kills_needs_an_air_kill_in_the_type() -> None:
    rows = [s(aircraft=1, air=1), s(aircraft=2), s(aircraft=2, air=2), s(aircraft=1, air=3)]

    assert types_with_kills(rows) == [1.0, 1.0, 2.0, 2.0]
    assert tiers("types_with_kills", [s(aircraft=n, air=1) for n in range(4)]) == {1: 1, 2: 3}


def test_landings_in_a_row_follow_the_ironman_conventions() -> None:
    rows = [
        s(),  # 1
        s(),  # 2
        s(grounded=True),  # never took off: neutral
        s(),  # 3
        s(landed=False),  # bailed out / ditched / crashed: ends the run
        s(),  # 1
        s(landed=False, mission_end=True),  # cut off at mission end in the air: neutral
        s(),  # 2
        s(death=True),  # ends it
        s(),  # 1
        s(captured=True),  # ends it
    ]

    assert landings_in_a_row(rows) == [1.0, 2.0, 2.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0]  # running best
    assert tiers("landing_streak", [s() for _ in range(6)]) == {1: 2, 2: 5}
    assert tiers("landing_streak", [s(), s(), s(landed=False), s(), s()]) == {}


def test_landing_that_ended_at_mission_end_on_the_ground_counts() -> None:
    assert tiers("landing_streak", [s(mission_end=True) for _ in range(3)]) == {1: 2}  # outcome landed: a landing


def test_ace_in_a_day_sums_the_utc_day() -> None:
    rows = [s(0, day=0, air=1), s(1, day=0, air=1), s(2, day=0, air=1), s(3, day=1, air=2)]

    assert kills_in_a_day(rows) == [1.0, 2.0, 3.0, 3.0]
    assert tiers("ace_in_a_day", rows) == {}  # 3 kills on the first day, 2 on the next: no ace yet
    # the day is the UTC day of the spawn: 23:50 and 00:10 are two days
    late = replace(s(0, air=2), spawned_at=datetime(2026, 9, 7, 23, 50, tzinfo=UTC))
    early = replace(s(1, air=2), spawned_at=datetime(2026, 9, 8, 0, 10, tzinfo=UTC))
    assert kills_in_a_day([late, early]) == [2.0, 2.0]
    assert tiers("ace_in_a_day", [s(0, air=3), s(1, air=2, death=True)]) == {1: 1}  # the fatal sortie's kills count


@pytest.mark.parametrize("a", [a for a in ACHIEVEMENTS if a.kind == "ribbon"], ids=lambda a: a.key)
def test_no_sorties_no_ribbon(a: Achievement) -> None:
    assert earn(a, []) == []


def test_ground_hot_streak_is_the_best_ground_burst_of_one_sortie() -> None:
    rows = [s(multi_kill_ground=12), s(multi_kill_ground=30), s(multi_kill_ground=5), s(multi_kill_ground=100)]

    assert BY_KEY["multi_kill_ground"].thresholds == (10, 25, 50, 100)
    assert tiers("multi_kill_ground", rows) == {1: 0, 2: 1, 3: 3, 4: 3}
    assert tiers("multi_kill_ground", [s(multi_kill_ground=9)]) == {}


def attack(n: int, points: float, seconds: float) -> AchievementSortie:
    return s(n, ground_points=points, time_on_target_s=seconds, is_attack=True)


def test_attack_rate_is_points_per_hour_on_target_once_the_minimum_time_is_there() -> None:
    assert MIN_TIME_ON_TARGET_S == 600.0
    # 100 points in 5 minutes: not enough time on target, the rate does not count yet (0)
    rows = [attack(0, 100, 300), attack(1, 100, 300), attack(2, 0, 3600)]

    # (200 / 600 s = 1200 / h), then (200 / 4200 s = about 171 / h): the running maximum keeps the best
    assert attack_rate(rows) == pytest.approx([0.0, 1200.0, 1200.0])


def test_attack_rate_reads_attack_sorties_only_and_never_falls() -> None:
    air = s(0, ground_points=5000.0, time_on_target_s=3600.0)  # an air sortie: neither points nor time count
    rows = [air, attack(1, 400, 1200), attack(2, -300, 1200)]

    assert attack_rate(rows) == pytest.approx([0.0, 1200.0, 1200.0])
    assert tiers("attack_rate_peak", rows) == {1: 1, 2: 1, 3: 1}
    assert tiers("attack_rate_peak", [attack(0, 100, 599.0)]) == {}
    assert BY_KEY["attack_rate_peak"].thresholds == (300, 600, 1000, 2500)


# --- life_kills_ground (maintainer 2026-11-02: Charmed Life in a ground version, Flak Dodger) ---


def attack_life(n: int, ground: int, *, death: bool = False, captured: bool = False) -> AchievementSortie:
    return s(n, is_attack=True, kills_ground=ground, death=death, captured=captured)


def test_flak_dodger_adds_up_the_ground_kills_of_one_life() -> None:
    assert BY_KEY["life_kills_ground"].thresholds == (100, 250, 500, 1000)
    assert tiers("life_kills_ground", [attack_life(0, 60), attack_life(1, 60), attack_life(2, 140)]) == {1: 1, 2: 2}


def test_flak_dodger_counts_the_fatal_sortie_then_starts_over() -> None:
    rows = [attack_life(0, 60), attack_life(1, 40, death=True), attack_life(2, 60)]
    assert tiers("life_kills_ground", rows) == {1: 1}
    assert tiers("life_kills_ground", [attack_life(0, 90, captured=True), attack_life(1, 90)]) == {}


def test_flak_dodger_follows_the_ground_track_alone() -> None:
    """A fighter's strafing adds nothing and losing a fighter ends nothing: Charmed Life the other way round."""
    rows = [attack_life(0, 60), s(1, kills_ground=500, death=True), attack_life(2, 40)]
    assert tiers("life_kills_ground", rows) == {1: 2}


def test_charmed_life_is_untouched_by_flak_dodger() -> None:
    assert tiers("life_kills", [s(0, air=3), attack_life(1, 500, death=True), s(2, air=2)]) == {1: 2}


def test_limping_home_has_a_platinum_tier() -> None:
    assert BY_KEY["damaged_landing"].thresholds == (1, 3, 5, 10)
