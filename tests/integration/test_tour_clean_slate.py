"""A new tour is a clean slate (maintainer, 2026-10-05; doc 17, doc 16): streaks and achievements restart in every tour
and the all-time rows are rolled up from the tour rows (best streak = max over tours, medal tier = max over tours, with
the sum of the tours' totals for the cumulative medals)."""

from datetime import timedelta

import pytest

from il2ks.core.replay.result import SortieResult
from il2ks.db.models import (
    PlayerAchievement,
    PlayerBestStreak,
    PlayerSortie,
    PlayerStreak,
    PlayerStreakRun,
    SiteSettings,
    Tour,
)
from il2ks.ingest.aggregates import rebuild_aggregates
from tests.factories import STARTED_AT, meta, mission, save, sortie
from tests.integration.test_achievements import held, pk
from tests.integration.test_achievements import snapshot as achievement_snapshot
from tests.integration.test_killboard_streaks import snapshot as streak_snapshot

pytestmark = pytest.mark.django_db

OCTOBER = STARTED_AT + timedelta(days=30)
NOVEMBER = STARTED_AT + timedelta(days=62)


def died(index: int, player: int = 1) -> SortieResult:
    return sortie(index, player, is_death=True, outcome="shot_down")


def survived(count: int, player: int = 1, first: int = 0) -> tuple[SortieResult, ...]:
    return tuple(sortie(first + i, player) for i in range(count))


def tours() -> list[Tour]:
    return list(Tour.objects.order_by("started_at"))


def everything() -> list[object]:
    runs = PlayerStreakRun.objects.order_by("player_id", "tour_id", "since").values_list(
        "player_id", "tour_id", "sorties", "kills_air", "flight_time_s", "since", "until", "ended_by", "ended_sortie_id"
    )
    return [*streak_snapshot(), *runs, *achievement_snapshot()]


def test_a_streak_ends_at_the_tour_boundary() -> None:
    save(mission(survived(3)), meta("m1", STARTED_AT))
    save(mission(survived(3)), meta("m2", OCTOBER))

    rows = PlayerStreakRun.objects.filter(player=pk(1), tour=None).order_by("since")
    assert [(r.sorties, r.ended_by) for r in rows] == [(3, "open"), (3, "open")]  # the union, never merged
    best = PlayerBestStreak.objects.get(player=pk(1), tour=None, kind="sorties")
    assert best.sorties == 3
    assert best.since.date() == STARTED_AT.date()  # a tie: the earliest wins
    assert PlayerStreak.objects.get(player=pk(1)).current_sorties == 3  # the October run, not 6


def test_all_time_best_streak_is_the_max_of_the_tour_bests() -> None:
    save(mission((*survived(2), died(2), *survived(3, first=3))), meta("m1", STARTED_AT))  # best 3
    save(mission(survived(5)), meta("m2", OCTOBER))  # best 5
    save(mission(survived(2, first=0)), meta("m3", NOVEMBER))  # best 2

    best = PlayerStreak.objects.get(player=pk(1))
    assert best.best_sorties == 5
    assert PlayerBestStreak.objects.get(player=pk(1), tour=None, kind="sorties").sorties == 5
    assert PlayerBestStreak.objects.get(player=pk(1), tour=None, kind="flight_time").flight_time_s == 3000.0
    assert PlayerStreakRun.objects.filter(player=pk(1), tour=None).count() == 4  # 2, 3, 5, 2


def test_current_streak_is_the_run_in_the_current_tour_and_a_new_tour_starts_at_zero() -> None:
    save(mission(survived(3)), meta("m1", STARTED_AT))
    assert PlayerStreak.objects.get(player=pk(1)).current_sorties == 3

    save(mission(survived(1, player=2)), meta("m2", OCTOBER))  # player 1 has not flown in October yet

    streak = PlayerStreak.objects.get(player=pk(1))
    assert (streak.current_sorties, streak.best_sorties) == (0, 3)
    save(mission(survived(1)), meta("m3", OCTOBER + timedelta(days=1)))
    assert PlayerStreak.objects.get(player=pk(1)).current_sorties == 1


def test_types_flown_resets_per_tour_and_all_time_is_the_max() -> None:
    save(mission((sortie(0, 1), sortie(1, 1, aircraft_type="F-86A-5"))), meta("m1", STARTED_AT))
    save(
        mission((sortie(0, 1, aircraft_type="P-51D-15"), sortie(1, 1, aircraft_type="IL-10"))), meta("m2", OCTOBER)
    )  # 2 + 2 = 4 types over the career, but never 3 in one tour

    assert "types_flown" not in held(1)
    september, october = tours()
    assert "types_flown" not in held(1, september)
    assert "types_flown" not in held(1, october)

    save(
        mission((sortie(0, 1, aircraft_type="MiG-15bis"), sortie(1, 1, aircraft_type="P-51D-15"))), meta("m3", NOVEMBER)
    )
    save(mission((sortie(0, 1, aircraft_type="IL-10"),)), meta("m4", NOVEMBER + timedelta(hours=1)))
    assert held(1)["types_flown"] == 1  # three types in November
    november = tours()[2]
    all_time = PlayerAchievement.objects.get(player_id=pk(1), tour=None, key="types_flown", tier=1)
    in_november = PlayerAchievement.objects.get(player_id=pk(1), tour=november, key="types_flown", tier=1)
    assert (all_time.sortie_id, all_time.earned_at) == (in_november.sortie_id, in_november.earned_at)


def test_a_max_medal_is_earned_when_its_first_tour_earned_it() -> None:
    save(mission((sortie(0, 1, kills_air=2),)), meta("m1", STARTED_AT))  # sortie_kills 2 in September
    save(mission((sortie(0, 1, kills_air=2),)), meta("m2", OCTOBER))

    row = PlayerAchievement.objects.get(player_id=pk(1), tour=None, key="sortie_kills", tier=1)
    assert row.mission.mission_uid == "m1"


def test_a_cumulative_medal_crosses_its_threshold_across_two_tours() -> None:
    save(mission((sortie(0, 1, kills_air=30),)), meta("m1", STARTED_AT))
    save(
        mission((sortie(0, 1, kills_air=10), sortie(1, 1, kills_air=10), sortie(2, 1, kills_air=10))),
        meta("m2", OCTOBER),
    )

    assert held(1)["career_kills"] == 2  # 30 + 30 = 60 kills: the 50 of tier 2 (5 x 10, OQ-128)
    for tour in tours():
        assert held(1, tour)["career_kills"] == 2  # 30 kills in a tour: the 10 of tier 2, not the 50
    row = PlayerAchievement.objects.get(player_id=pk(1), tour=None, key="career_kills", tier=2)
    assert row.mission.mission_uid == "m2"
    in_tour = PlayerAchievement.objects.get(key="career_kills", tour=tours()[1], tier=1)
    sorties = list(PlayerSortie.objects.filter(mission=row.mission_id, player_id=pk(1)).order_by("spawned_at", "pk"))
    assert row.sortie_id == sorties[1].pk  # 30 + 10 = 40, then 30 + 20 = 50: the second October sortie
    assert in_tour.sortie_id == sorties[0].pk  # the tour's own first kill


def test_the_rows_are_the_same_incremental_and_rebuilt() -> None:
    save(mission((*survived(2), died(2), sortie(3, 2, kills_air=5))), meta("m1", STARTED_AT))
    save(mission((sortie(0, 1, kills_air=6), *survived(4, first=1))), meta("m2", OCTOBER))
    save(mission((sortie(0, 1, kills_air=6, aircraft_type="IL-10"),)), meta("m3", NOVEMBER))
    incremental = everything()
    assert PlayerBestStreak.objects.filter(tour=None).exists()
    assert PlayerAchievement.objects.filter(tour=None).exists()

    rebuild_aggregates()

    assert everything() == incremental


def test_a_late_import_into_an_old_tour_gives_the_rebuilt_rows() -> None:
    save(mission((sortie(0, 1, kills_air=30), *survived(2, first=1))), meta("m2", OCTOBER))
    save(mission((sortie(0, 1, kills_air=30), *survived(4, first=1))), meta("m1", STARTED_AT))  # older, imported later
    incremental = everything()

    rebuild_aggregates()

    assert everything() == incremental
    row = PlayerAchievement.objects.get(player_id=pk(1), tour=None, key="career_kills", tier=2)
    assert row.mission.mission_uid == "m2"  # September's 30 come first now: 60 (the 50) is reached in October


def test_weeks_in_a_row_reset_per_tour_and_all_time_is_the_max() -> None:
    for uid, days in (("w1", 0), ("w2", 7), ("w3", 14), ("w4", 21)):  # four consecutive weeks, two per tour
        save(mission((sortie(0, 1),)), meta(uid, STARTED_AT + timedelta(days=days)))

    assert held(1).get("regular") == 1  # two weeks in a row (the bronze tier) in each tour, never four
    for tour in tours():
        assert held(1, tour).get("regular") == 1


def test_the_upgrade_backfill_rolls_the_old_all_time_rows_up_once() -> None:
    from il2ks.ops import migrate

    save(mission(survived(3)), meta("m1", STARTED_AT))
    save(mission(survived(3)), meta("m2", OCTOBER))
    good = everything()
    PlayerStreak.objects.update(best_sorties=6, current_sorties=6, current_tour=None)  # what the old code stored
    PlayerBestStreak.objects.filter(tour=None, kind="sorties").update(sorties=6)
    PlayerAchievement.objects.filter(tour=None).delete()

    migrate._backfill_tour_clean_slate()  # pyright: ignore[reportPrivateUsage]

    assert everything() == good
    assert migrate.BACKFILL_TOUR_CLEAN_SLATE in SiteSettings.objects.get(pk=1).backfills_done
