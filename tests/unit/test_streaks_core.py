"""Ironman streak rule (FR-WEB-23, doc 13): which sorties extend, break or skip a streak. Pure core, no database."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from il2ks.core.streaks import RunEnd, StreakSortie, Track, runs, summarize, track_of

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def s(
    n: int,
    *,
    kills: int = 0,
    flight: float = 600.0,
    death: bool = False,
    captured: bool = False,
    grounded: bool = False,
    track: Track = Track.AIR,
    ground_kills: int = 0,
) -> StreakSortie:
    start = T0 + timedelta(hours=n)
    return StreakSortie(
        start, start + timedelta(minutes=30), kills, flight, death, captured, grounded, n, ground_kills, track
    )


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
    assert (result.best_kills.sorties, result.best_kills.kills_air) == (2, 5)
    assert (result.best_flight_time.sorties, result.best_flight_time.flight_time_s) == (1, 7200.0)


def test_best_by_air_kills_breaks_ties_by_sorties_then_by_the_earlier_run() -> None:
    longer = summarize([s(0, kills=2), s(1, death=True), s(2, kills=2), s(3), s(4)])
    full_tie = summarize([s(0, kills=1), s(1, death=True), s(2, kills=1)])

    assert longer.best_kills.sorties == 3  # same kills, the longer run wins
    assert longer.best_kills.since == s(2).spawned_at
    assert full_tie.best_kills.since == s(0).spawned_at  # the earlier run keeps a full tie
    assert full_tie.best_flight_time.since == s(0).spawned_at


def test_no_survived_sortie_means_empty_bests() -> None:
    result = summarize([s(0, death=True)])

    assert result.best_kills.sorties == 0 == result.best_flight_time.sorties


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


def test_runs_lists_every_run_of_two_or_more_with_how_it_ended() -> None:
    """OQ-82: all runs, not just the best; a one-sortie run is not listed; the ending sortie is named."""
    flown = [
        s(0, kills=1),
        s(1),
        replace(s(2, death=True), ref=12),
        s(3),  # a run of one: not listed
        replace(s(4, captured=True), ref=14),
        s(5, kills=2),
        s(6, grounded=True),  # neutral: skipped
        s(7),
        s(8, flight=100.0),
    ]
    result = runs(flown)
    assert [(r.streak.sorties, r.streak.kills_air, r.end, r.ended_by_ref) for r in result] == [
        (2, 1, RunEnd.DEATH, 12),
        (3, 2, RunEnd.OPEN, None),
    ]
    assert result[1].streak.flight_time_s == 600.0 * 2 + 100.0
    assert [r.streak.sorties for r in runs(flown, minimum=1)] == [2, 1, 3]
    assert runs([]) == []


# --- the two tracks (maintainer, 2026-10-05) ------------------------------------------------------------------------
def test_track_of_a_combat_role() -> None:
    assert track_of("attack") is Track.GROUND
    assert track_of("air_superiority") is Track.AIR
    assert track_of(None) is Track.AIR  # no combat role known: the air track, as before the split


def test_an_attack_sortie_death_ends_the_ground_run_only() -> None:
    sorties = [s(0), s(1), s(2, track=Track.GROUND), s(3, track=Track.GROUND, death=True), s(4)]

    air = summarize(sorties, Track.AIR)
    ground = summarize(sorties, Track.GROUND)

    assert (air.current.sorties, air.best.sorties) == (3, 3)  # the ground death did not touch the air run
    assert (ground.current.sorties, ground.best.sorties) == (0, 1)


def test_an_air_sortie_death_ends_the_air_run_only() -> None:
    sorties = [s(0, track=Track.GROUND), s(1), s(2, death=True), s(3, track=Track.GROUND), s(4)]

    assert summarize(sorties, Track.AIR).current.sorties == 1
    assert summarize(sorties, Track.GROUND).current.sorties == 2  # the air death did not touch the ground run


def test_the_ground_track_counts_ground_kills_and_ranks_by_them() -> None:
    sorties = [
        s(0, track=Track.GROUND, ground_kills=2, kills=1),
        s(1, track=Track.GROUND, death=True),
        s(2, track=Track.GROUND, ground_kills=5),
        s(3, kills=9),
    ]

    ground = summarize(sorties, Track.GROUND)
    air = summarize(sorties, Track.AIR)

    assert (ground.best_kills.kills_ground, ground.best_kills.sorties) == (5, 1)
    assert (air.best_kills.kills_air, air.best_kills.sorties) == (9, 1)  # only the air-track sortie counts for air


def test_runs_are_per_track() -> None:
    sorties = [s(0), s(1, track=Track.GROUND), s(2), s(3, track=Track.GROUND), s(4, track=Track.GROUND, death=True)]

    assert [r.streak.sorties for r in runs(sorties, Track.AIR)] == [2]
    ground = runs(sorties, Track.GROUND)
    assert [(r.streak.sorties, r.end, r.ended_by_ref) for r in ground] == [(2, RunEnd.DEATH, 4)]


# --- the all track: no filter (maintainer, 2026-10-05) --------------------------------------------------------------
def test_the_all_track_run_spans_attack_and_air_sorties_and_ends_at_any_loss() -> None:
    sorties = [
        s(0),
        s(1, track=Track.GROUND, ground_kills=2),
        s(2, kills=1),
        s(3, track=Track.GROUND, death=True),  # an attack death ends the all run (it left the air run alone)
        s(4),
        s(5, track=Track.GROUND, captured=True),
        s(6, death=True),
    ]

    everything = summarize(sorties, Track.ALL)

    assert (everything.best.sorties, everything.best.kills_air, everything.best.kills_ground) == (3, 1, 2)
    assert everything.current.sorties == 0
    assert [(r.streak.sorties, r.end, r.ended_by_ref) for r in runs(sorties, Track.ALL)] == [(3, RunEnd.DEATH, 3)]
    assert summarize(sorties, Track.AIR).best.sorties == 3  # the air run went on through the attack death: 0, 2, 4


def test_the_all_track_ranks_its_kills_by_air_plus_ground_kills() -> None:
    sorties = [s(0, kills=1, ground_kills=1), s(1, death=True), s(2, track=Track.GROUND, ground_kills=3), s(3, kills=1)]

    best = summarize(sorties, Track.ALL).best_kills

    assert (best.sorties, best.kills_air, best.kills_ground, best.kills_of(Track.ALL)) == (2, 1, 3, 4)
