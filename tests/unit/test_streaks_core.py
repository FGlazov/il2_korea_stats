"""Ironman streak rule (FR-WEB-23, doc 13): which sorties extend, break or skip a streak. Pure core, no database."""

from datetime import UTC, datetime, timedelta

from il2ks.core.streaks import StreakSortie, summarize

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def s(
    n: int,
    *,
    kills: int = 0,
    flight: float = 600.0,
    death: bool = False,
    captured: bool = False,
    grounded: bool = False,
) -> StreakSortie:
    start = T0 + timedelta(hours=n)
    return StreakSortie(start, start + timedelta(minutes=30), kills, flight, death, captured, grounded)


def test_best_by_air_kills_and_by_flight_time_are_their_own_streaks() -> None:
    result = summarize(
        [
            s(0, kills=1),
            s(1),
            s(2),
            s(3, death=True),
            s(4, kills=3),
            s(5, kills=2),
            s(6, death=True),
            s(7, flight=7200.0),
        ]
    )

    assert (result.best.sorties, result.best.kills_air) == (3, 1)
    assert (result.best_air_kills.sorties, result.best_air_kills.kills_air) == (2, 5)
    assert (result.best_flight_time.sorties, result.best_flight_time.flight_time_s) == (1, 7200.0)


def test_best_by_air_kills_breaks_ties_by_sorties_then_by_the_earlier_run() -> None:
    longer = summarize([s(0, kills=2), s(1, death=True), s(2, kills=2), s(3), s(4)])
    full_tie = summarize([s(0, kills=1), s(1, death=True), s(2, kills=1)])

    assert longer.best_air_kills.sorties == 3  # same kills, the longer run wins
    assert longer.best_air_kills.since == s(2).spawned_at
    assert full_tie.best_air_kills.since == s(0).spawned_at  # the earlier run keeps a full tie
    assert full_tie.best_flight_time.since == s(0).spawned_at


def test_no_survived_sortie_means_empty_bests() -> None:
    result = summarize([s(0, death=True)])

    assert result.best_air_kills.sorties == 0 == result.best_flight_time.sorties


def test_no_sorties_no_streak() -> None:
    result = summarize([])

    assert result.current.sorties == 0 == result.best.sorties
    assert result.current.since is None


def test_survived_sorties_extend_and_sum() -> None:
    result = summarize([s(0, kills=2), s(1, kills=1, flight=300.0), s(2)])

    assert (result.current.sorties, result.current.kills_air, result.current.flight_time_s) == (3, 3, 1500.0)
    assert result.current.since == s(0).spawned_at
    assert result.current.until == s(2).ended_at
    assert result.best == result.current


def test_death_ends_the_streak_and_is_not_part_of_it() -> None:
    result = summarize([s(0), s(1, kills=1), s(2, kills=5, death=True), s(3)])

    assert result.best.sorties == 2
    assert result.best.kills_air == 1  # the fatal sortie's kills are not in the streak
    assert result.current.sorties == 1


def test_capture_breaks_like_a_death() -> None:
    result = summarize([s(0), s(1, captured=True)])

    assert result.current.sorties == 0
    assert result.best.sorties == 1


def test_latest_sortie_fatal_means_no_current_streak() -> None:
    result = summarize([s(0), s(1), s(2, death=True)])

    assert result.current.sorties == 0
    assert result.current.since is None
    assert result.best.sorties == 2


def test_sortie_that_never_took_off_is_skipped() -> None:
    result = summarize([s(0), s(1, grounded=True, flight=0.0), s(2)])

    assert result.current.sorties == 2  # neither counted nor breaking


def test_grounded_death_still_breaks() -> None:
    """A taxi accident that kills the pilot is a death (is_death), even though the sortie never took off."""
    result = summarize([s(0), s(1, grounded=True, death=True), s(2)])

    assert result.best.sorties == 1
    assert result.current.sorties == 1


def test_best_tie_goes_to_more_kills_then_the_earlier_streak() -> None:
    more_kills = summarize([s(0), s(1, death=True), s(2, kills=2), s(3, death=True)])
    assert more_kills.best.kills_air == 2
    assert more_kills.best.since == s(2).spawned_at

    full_tie = summarize([s(0), s(1, death=True), s(2), s(3, death=True)])
    assert full_tie.best.since == s(0).spawned_at


def test_best_can_be_the_running_streak() -> None:
    result = summarize([s(0), s(1, death=True), s(2), s(3), s(4)])

    assert result.best == result.current
    assert result.best.sorties == 3
