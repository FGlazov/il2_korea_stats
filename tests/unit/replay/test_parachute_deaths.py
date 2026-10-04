"""Parachute deaths (OQ-99): a pilot killed after leaving the aircraft in the air is a death, never "captured".

The old `[rules] parachute_deaths = false` toggle was removed (the maintainer: "should be death if a pilot is killed
while parachuting"); the config key is still accepted with a warning (tests/unit/test_config_rules.py)."""

from il2ks.core.logparse.events import Pos
from il2ks.core.replay.result import SortieResult
from tests.unit.replay.builder import FAR, NO, Scenario, by_acct

DOWN = Pos(FAR.x + 300.0, FAR.y - 1_500.0, FAR.z)


def _bail_and_get_shot(sc: Scenario) -> None:
    """A's aircraft is destroyed, the pilot ejects, and B shoots him 20 s later (AType 3 of the pilot bot)."""
    sc.fly_a()
    sc.fly_b()
    sc.kill(110, NO, 100, pos=FAR)
    sc.declare(110.2, 101, "BotPlanePilot_Test", 601, parent=-1, pos=FAR)  # the ejection spawn
    sc.end(110.2, 0, 101)
    sc.kill(130, 200, 101, pos=DOWN)
    sc.remove_bot(131, 101, DOWN)


def test_pilot_killed_under_the_parachute_dies() -> None:
    sc = Scenario()
    _bail_and_get_shot(sc)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.is_death, a.pilot_status, a.is_captured) == ("in_aircraft", True, "dead", False)


def test_a_pilot_killed_in_the_seat_dies() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.kill(110, NO, 100, pos=FAR)
    sc.kill(110.1, NO, 101, pos=FAR)
    sc.end(110.1, 0, 101)
    sc.remove_bot(111, 101, FAR)
    a: SortieResult = by_acct(sc.result(), 1)
    assert (a.is_death, a.pilot_status) == (True, "dead")


def test_a_bailout_nobody_shot_is_not_a_death() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.kill(110, NO, 100, pos=FAR)
    sc.declare(110.2, 101, "BotPlanePilot_Test", 601, parent=-1, pos=FAR)
    sc.end(110.2, 0, 101)
    sc.remove_bot(140, 101, DOWN)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.is_death, a.is_plane_lost) == ("bailed_out", False, True)
