"""Resupply (FR-ING-24): a landing followed by another takeoff in the same sortie may mean the aircraft was rearmed."""

import pytest

from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import MissionResult
from tests.unit.replay.builder import Scenario, by_acct


def _two_legs(rules: ReplayRules | None = None) -> MissionResult:
    """Takeoff, landing, a second takeoff and a second landing in one sortie (AType 5, 6, 5, 6, then AType 4)."""
    sc = Scenario()
    sc.fly_a()
    sc.land(100, 100)
    sc.takeoff(200, 100)
    sc.land(300, 100)
    sc.end(310, 100, 101)
    return sc.result(rules)


def test_landing_followed_by_another_takeoff_is_a_resupply() -> None:
    a = by_acct(_two_legs(), 1)
    assert (a.takeoffs, a.landings, a.resupplied) == (2, 2, True)


def test_resupply_can_be_switched_off() -> None:
    a = by_acct(_two_legs(ReplayRules(resupply_allowed=False)), 1)
    assert (a.takeoffs, a.landings, a.resupplied) == (2, 2, False)


def test_default_allows_resupply() -> None:
    assert ReplayRules().resupply_allowed is True


@pytest.mark.parametrize("lands", [True, False])
def test_single_flight_is_not_a_resupply(*, lands: bool) -> None:
    sc = Scenario()
    sc.fly_a()
    if lands:
        sc.land(100, 100)
    sc.end(110, 100, 101)
    assert not by_acct(sc.result(), 1).resupplied


def test_takeoff_before_the_landing_is_not_a_resupply() -> None:
    """An air start (no AType 5) and one landing: nothing follows the landing."""
    sc = Scenario()
    sc.player(0, 100, 101, 1, in_air=0)
    sc.land(100, 100)
    sc.end(110, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.landings, a.resupplied) == (1, False)
