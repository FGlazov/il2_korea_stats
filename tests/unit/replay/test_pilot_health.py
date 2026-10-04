"""Pilot damage and aircraft damage per sortie (maintainer, OQ-115): sums capped at 1, overrides on death / destruction."""

import pytest

from il2ks.core.replay.result import MissionResult
from tests.unit.replay.builder import Scenario, by_acct


def _flight(*, bot_damage: tuple[float, ...] = (), air_damage: tuple[float, ...] = ()) -> Scenario:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    for i, amount in enumerate(bot_damage):
        sc.damage(100 + i, 200, 101, amount)
    for i, amount in enumerate(air_damage):
        sc.damage(100 + i, 200, 100, amount)
    return sc


def _result(sc: Scenario) -> MissionResult:
    sc.end(150, 100, 101)
    return sc.result()


def test_pilot_without_damage_has_full_health() -> None:
    a = by_acct(_result(_flight()), 1)
    assert (a.pilot_damage, a.damage_taken) == (0.0, 0.0)


def test_pilot_damage_is_summed_from_the_pilot_bot_only() -> None:
    a = by_acct(_result(_flight(bot_damage=(0.1, 0.25), air_damage=(0.4,))), 1)
    assert a.pilot_damage == pytest.approx(0.35)
    assert a.damage_taken == pytest.approx(0.4)
    assert (a.pilot_status, a.is_death) == ("wounded", False)


def test_damage_is_capped_at_one_without_a_death() -> None:
    a = by_acct(_result(_flight(bot_damage=(0.7, 0.6), air_damage=(0.8, 0.8))), 1)
    assert (a.pilot_damage, a.damage_taken) == (1.0, 1.0)


def test_dead_pilot_has_full_damage_whatever_the_lines_say() -> None:
    sc = _flight(bot_damage=(0.2,))
    sc.kill(100.2, 200, 101)
    a = by_acct(_result(sc), 1)
    assert a.is_death
    assert a.pilot_damage == 1.0


def test_destroyed_aircraft_has_full_damage_whatever_the_lines_say() -> None:
    sc = _flight(air_damage=(0.3,))
    sc.kill(110, 200, 100)
    a = by_acct(_result(sc), 1)
    assert a.aircraft_status == "destroyed"
    assert a.damage_taken == 1.0
