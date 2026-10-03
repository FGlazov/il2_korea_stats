"""A player aircraft "destroyed" on the parking spot that takes off later (design_doc/13_game_rules.md, Objects).

Real shape (seen in the sample logs, e.g. 69 s after the spawn): AType 2 (`AID:-1`, damage 1.0), an AType 12
re-declaration of the same ID with a non-`-1` `MID`, AType 3 (`AID:-1`), all on one tick. Minutes later the same
aircraft takes off, flies, lands and ends the sortie normally. A destroyed aircraft can't take off, so that AType 3
was a reset.
"""

import pytest

from il2ks.core.replay.result import MissionResult
from tests.unit.replay.builder import GROUND, NO, Scenario, by_acct


def _parked_reset(*, takes_off_later: bool) -> MissionResult:
    sc = Scenario()
    sc.player(0, 100, 101, 1)
    sc.damage(69, NO, 100, 1.0, pos=GROUND)
    sc.declare(69, 100, "F-86A-5", 601, pos=GROUND)  # the same-tick AType 12 re-declaration
    sc.kill(69, NO, 100, pos=GROUND)
    if takes_off_later:
        sc.takeoff(210, 100)
        sc.land(1650, 100)
    sc.end(1660, 100, 101)
    return sc.result()


def test_aircraft_that_takes_off_after_its_destruction_was_never_lost() -> None:
    result = _parked_reset(takes_off_later=True)
    a = by_acct(result, 1)
    assert (a.outcome, a.pilot_fate) == ("landed", "in_aircraft")
    assert (a.is_plane_lost, a.is_death, a.loss_cause, a.pilot_status) == (False, False, "none", "healthy")
    assert (a.takeoffs, a.landings) == (1, 1)
    assert a.takeoff_tick == 210 * 50
    assert a.flight_time_s == pytest.approx(1440.0)
    assert (a.aircraft_status, a.damage_taken) == ("unharmed", 0.0)  # the reset's 1.0 damage goes with it
    assert result.kills == ()
    assert [e.kind for e in a.timeline] == ["spawn", "takeoff", "landing", "sortie_end"]


def test_aircraft_destroyed_on_the_parking_spot_without_a_takeoff_is_still_lost() -> None:
    a = by_acct(_parked_reset(takes_off_later=False), 1)
    assert (a.outcome, a.is_plane_lost, a.loss_cause, a.takeoffs, a.takeoff_tick) == ("crashed", True, "self", 0, None)


def test_a_real_destruction_after_the_takeoff_still_counts() -> None:
    """Reset first, then a genuine shoot-down after the flight started: the second AType 3 is a loss."""
    sc = Scenario()
    sc.fly_b()
    sc.player(0, 100, 101, 1)
    sc.damage(69, NO, 100, 1.0, pos=GROUND)
    sc.kill(69, NO, 100, pos=GROUND)
    sc.takeoff(210, 100)
    sc.damage(500, 200, 100, 1.0)
    sc.kill(500, 200, 100)
    sc.end(500.1, 100, 101)
    result = sc.result()
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.outcome, a.loss_cause, a.takeoffs) == ("shot_down", "attacker", 1)
    assert a.flight_time_s == pytest.approx(290.0)
    assert b.kills_air == 1


def test_takeoff_after_the_sortie_ended_does_not_revive_a_lost_aircraft() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.kill(100, NO, 100)
    sc.end(100.5, 100, 101)
    sc.takeoff(300, 100)  # debris or a later sortie: not this one
    a = by_acct(sc.result(), 1)
    assert (a.outcome, a.is_plane_lost) == ("crashed", True)


@pytest.mark.parametrize("takes_off_later", [True, False])
def test_takeoff_tick_and_takeoff_count_agree(*, takes_off_later: bool) -> None:
    a = by_acct(_parked_reset(takes_off_later=takes_off_later), 1)
    assert (a.takeoff_tick is not None) == (a.takeoffs > 0)
