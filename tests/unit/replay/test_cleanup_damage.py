"""Damage logged at or after AType 7 is the server's despawn cleanup, not combat (design_doc/13_game_rules.md).

Real shape (115 sorties in the 210 samples had `damage_taken` 1.0 without a loss; 105 of them are this): a sortie
force-ended by the mission end gets AType 2 `AID:-1` damage 1.0 and an AType 3 a few ticks after AType 7.
"""

import pytest

from il2ks.core.replay.result import SortieResult
from tests.unit.replay.builder import NO, Scenario, by_acct


def cleanup_scenario(*, earlier_damage: float) -> SortieResult:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    if earlier_damage:
        sc.damage(300, 200, 100, earlier_damage)
        sc.hit(300, 200, 100)
    sc.mission_end(1000)
    sc.damage(1000.1, NO, 100, 1.0)  # the despawn cleanup
    sc.hit(1000.1, 200, 100)
    sc.kill(1000.1, NO, 100)
    sc.end(1000.1, 100, 101)
    sc.end(1000.2, 200, 201)
    return by_acct(sc.result(), 1)


def test_cleanup_damage_is_not_damage_taken() -> None:
    a = cleanup_scenario(earlier_damage=0.0)
    assert (a.outcome, a.ended_by_mission_end) == ("airborne", True)
    assert (a.damage_taken, a.aircraft_status) == (0.0, "unharmed")
    assert (a.is_plane_lost, a.pilot_status) == (False, "healthy")
    assert a.damage == ()
    assert a.ammo_hits == ()


def test_real_damage_before_the_mission_end_is_kept() -> None:
    a = cleanup_scenario(earlier_damage=0.3)
    assert (a.damage_taken, a.aircraft_status) == (pytest.approx(0.3), "damaged")
    (exchange,) = a.damage
    assert exchange.damage_taken == pytest.approx(0.3)
    assert exchange.hits_taken == 1
    assert [(h.ammo, h.hits_received) for h in a.ammo_hits] == [("BULLET_12-7_USA_API", 1)]


def test_damage_after_the_end_of_a_sortie_that_was_not_forced_still_counts() -> None:
    """A destruction well after AType 7 on a sortie that really ended earlier is not cleanup."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(300, 200, 100, 0.5)
    sc.end(400, 100, 101)
    sc.mission_end(1000)
    a = by_acct(sc.result(), 1)
    assert a.damage_taken == pytest.approx(0.5)
