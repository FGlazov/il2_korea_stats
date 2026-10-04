"""Parachute deaths (OQ-61, `[rules] parachute_deaths`): a pilot killed after leaving the aircraft in the air.

On (the default): a death, as always. Off (the old "no parachute deaths" mod): the bailout still loses the aircraft, but
the pilot survives. Sample missions: 2 of 15,245 pilot sorties (design_doc/13_game_rules.md, Parachute deaths)."""

from il2ks.core.logparse.events import Pos
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import SortieResult
from il2ks.core.replay.toggles import RuleToggles
from tests.unit.replay.builder import FAR, NO, Scenario, by_acct

SURVIVE = ReplayRules(toggles=RuleToggles(parachute_deaths=False))
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


def test_pilot_killed_under_the_parachute_dies_by_default() -> None:
    sc = Scenario()
    _bail_and_get_shot(sc)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.is_death, a.pilot_status) == ("in_aircraft", True, "dead")


def test_no_parachute_deaths_keeps_the_pilot_alive_but_the_aircraft_lost() -> None:
    sc = Scenario()
    _bail_and_get_shot(sc)
    a = by_acct(sc.result(SURVIVE), 1)
    assert (a.pilot_fate, a.pilot_fate_source) == ("bailed_out", "event")
    assert (a.is_death, a.is_plane_lost) == (False, True)
    assert a.pilot_status != "dead"


def test_a_pilot_killed_in_the_seat_still_dies_with_the_toggle_off() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.kill(110, NO, 100, pos=FAR)
    sc.kill(110.1, NO, 101, pos=FAR)
    sc.end(110.1, 0, 101)
    sc.remove_bot(111, 101, FAR)
    a: SortieResult = by_acct(sc.result(SURVIVE), 1)
    assert (a.is_death, a.pilot_status) == (True, "dead")


def test_a_bailout_nobody_shot_is_the_same_either_way() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.kill(110, NO, 100, pos=FAR)
    sc.declare(110.2, 101, "BotPlanePilot_Test", 601, parent=-1, pos=FAR)
    sc.end(110.2, 0, 101)
    sc.remove_bot(140, 101, DOWN)
    for rules in (ReplayRules(), SURVIVE):
        a = by_acct(sc.result(rules), 1)
        assert (a.pilot_fate, a.is_death, a.is_plane_lost) == ("bailed_out", False, True)
