"""Replay scenario tests: capture on enemy territory and landing at friendly airfields (TD-21, il2_stats rules)."""

from il2ks.core.logparse.events import Pos
from il2ks.core.replay.areas import point_in_polygon
from tests.unit.replay.builder import FAR, GROUND, Scenario, by_acct

SQUARE = ((9_000.0, 9_000.0), (12_000.0, 9_000.0), (12_000.0, 11_000.0), (9_000.0, 11_000.0))
CHUTE = Pos(FAR.x + 500, FAR.y - 1500, FAR.z)  # inside SQUARE


def _bailout_scenario(area_country: int) -> Scenario:
    sc = Scenario()
    sc.area(1, area_country, SQUARE)
    sc.fly_a()  # coalition 2
    sc.end(110, 0, 101)
    sc.remove_bot(110, 101, CHUTE)
    sc.kill(110.5, -1, 100)
    return sc


def test_bailout_over_enemy_territory_is_captured() -> None:
    a = by_acct(_bailout_scenario(area_country=501).result(), 1)  # 501 = coalition 1 = enemy
    assert a.pilot_fate == "bailed_out"
    assert (a.is_captured, a.pilot_status) == (True, "captured")


def test_bailout_over_own_territory_is_not_captured() -> None:
    a = by_acct(_bailout_scenario(area_country=601).result(), 1)
    assert (a.is_captured, a.pilot_status) == (False, "healthy")


def test_landing_on_enemy_territory_is_captured() -> None:
    sc = Scenario()
    sc.area(1, 501, SQUARE)
    sc.fly_a()
    sc.land(100, 100, pos=FAR)
    sc.end(110, 100, 101, pos=FAR)
    assert by_acct(sc.result(), 1).is_captured


def test_landing_far_from_a_friendly_airfield_is_a_ditching() -> None:
    sc = Scenario()
    sc.airfield(1, 601, Pos(50_000.0, 10.0, 50_000.0))
    sc.fly_a()
    sc.land(100, 100)
    sc.end(110, 100, 101)
    assert by_acct(sc.result(), 1).outcome == "ditched"


def test_landing_at_a_friendly_airfield_is_a_landing() -> None:
    sc = Scenario()
    sc.airfield(1, 601, Pos(GROUND.x + 500, GROUND.y, GROUND.z))
    sc.airfield(2, 501, Pos(50_000.0, 10.0, 50_000.0))
    sc.fly_a()
    sc.land(100, 100)
    sc.end(110, 100, 101)
    assert by_acct(sc.result(), 1).outcome == "landed"


def test_point_in_polygon() -> None:
    assert point_in_polygon(10_000.0, 10_000.0, SQUARE)
    assert not point_in_polygon(20_000.0, 10_000.0, SQUARE)
    assert not point_in_polygon(10_000.0, 10_000.0, SQUARE[:2])  # fewer than 3 points
