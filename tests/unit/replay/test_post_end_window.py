"""`ReplayRules.post_end_destroy_window_s` (default 5 s): what a late destruction of a normally ended aircraft does.

A research run measured a 300 s window on the sample data. These tests pin both settings, so flipping the default in
`config.py` is a one-line change whose effect on the rules shows up here (FR-ING-22, doc 12 shot-down shape).
"""

import pytest

from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import MissionResult
from tests.unit.replay.builder import GROUND, Scenario, by_acct


def _late_destruction(rules: ReplayRules) -> MissionResult:
    """Player A lands, ends the sortie normally (AType 4 with the aircraft id) and enemy B destroys the parked
    aircraft 60 s later."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.land(100, 100)
    sc.end(110, 100, 101)
    sc.damage(169.5, 200, 100, 0.5, pos=GROUND)
    sc.kill(170, 200, 100, pos=GROUND)
    return sc.result(rules)


def test_default_window_is_five_seconds() -> None:
    assert ReplayRules().post_end_destroy_window_s == 5.0


def test_aircraft_destroyed_60_s_after_a_normal_end_is_ignored_with_a_5_s_window() -> None:
    result = _late_destruction(ReplayRules(post_end_destroy_window_s=5.0))
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.outcome, a.pilot_fate) == ("landed", "in_aircraft")
    assert (a.is_plane_lost, a.is_death, a.loss_cause) == (False, False, "none")
    assert (b.kills_air, result.kills) == (0, ())


def test_aircraft_destroyed_60_s_after_a_normal_end_is_a_loss_with_a_300_s_window() -> None:
    """Also a death of the pilot who left long ago: that's the price of the wider window."""
    result = _late_destruction(ReplayRules(post_end_destroy_window_s=300.0))
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.outcome, a.pilot_fate) == ("shot_down", "in_aircraft")
    assert (a.is_plane_lost, a.is_death, a.loss_cause, a.aircraft_status) == (True, True, "attacker", "destroyed")
    assert b.kills_air == 1
    assert [(k.killer_sortie_index, k.victim_sortie_index) for k in result.kills] == [(b.index, a.index)]


@pytest.mark.parametrize("window", [5.0, 300.0])
def test_destruction_inside_the_window_always_counts(window: float) -> None:
    """The shot-down shape (doc 12): AType 4 first, the aircraft's AType 3 a second later."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(99.5, 200, 100, 0.5)
    sc.end(100, 100, 101)
    sc.kill(101, 200, 100)
    a = by_acct(sc.result(ReplayRules(post_end_destroy_window_s=window)), 1)
    assert (a.outcome, a.is_plane_lost, a.loss_cause) == ("shot_down", True, "attacker")
