"""Replay scenario tests: friendly fire is tracked apart from kills, assists and ordinary hits (doc 06, FR-WEB-4).

Friendly = same non-zero coalition, and never the sortie's own aircraft, crew or turrets.
"""

import pytest

from tests.unit.replay.builder import Scenario, by_acct


def _scenario() -> Scenario:
    """A (601, coalition 2) and B (501, coalition 1) in the air, plus an AI F-86 (301) on A's side."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.declare(0, 301, "F-86A-5", 601)
    return sc


def test_friendly_kill_hits_and_damage_on_an_ai_aircraft() -> None:
    sc = _scenario()
    sc.hit(100, 100, 301)
    sc.hit(100.1, 100, 301)
    sc.damage(100.2, 100, 301, 0.4)
    sc.damage(100.3, 100, 301, 0.6)
    sc.kill(101, 100, 301)
    sc.end(200, 100, 101)
    result = sc.result()
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.friendly_kills, a.friendly_hits) == (1, 2)
    assert a.friendly_damage == pytest.approx(1.0)
    assert a.kills_air == 0  # not a normal kill
    assert (b.friendly_kills, b.friendly_hits, b.friendly_damage) == (0, 0, 0.0)


def test_hits_and_damage_on_the_enemy_are_not_friendly() -> None:
    sc = _scenario()
    sc.hit(100, 100, 200)
    sc.damage(100, 100, 200, 0.5)
    sc.end(200, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.friendly_kills, a.friendly_hits, a.friendly_damage) == (0, 0, 0.0)
    assert a.damage[0].hits_dealt == 1


def test_friendly_fire_between_two_players_counts_for_the_shooter_only() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly(600, 601, 4)  # D: same side as A
    sc.hit(100, 100, 600)
    sc.damage(100, 100, 600, 1.0)
    sc.kill(101, 100, 600)
    sc.end(101.1, 600, 601)
    sc.end(200, 100, 101)
    result = sc.result()
    a, d = by_acct(result, 1), by_acct(result, 4)
    assert (a.friendly_kills, a.friendly_hits, a.friendly_damage) == (1, 1, pytest.approx(1.0))
    assert (d.friendly_kills, d.friendly_hits, d.friendly_damage) == (0, 0, 0.0)
    assert (a.kills_air, a.assists) == (0, 0)


def test_own_aircraft_and_crew_are_not_friendly_objects() -> None:
    sc = _scenario()
    sc.hit(100, 101, 100)  # the pilot bot hits its own aircraft
    sc.damage(100, 100, 101, 0.2)  # the aircraft damages its own pilot
    sc.end(200, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.friendly_hits, a.friendly_damage) == (0, 0.0)


def test_explosion_hits_and_environment_damage_are_not_friendly_fire() -> None:
    sc = _scenario()
    sc.hit(100, 100, 301, ammo="explosion")
    sc.damage(100, -1, 301, 0.5)  # AID:-1, no attacker
    sc.end(200, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.friendly_hits, a.friendly_damage) == (0, 0.0)


def test_friendly_assist_is_not_a_friendly_kill() -> None:
    """Two shooters on one friendly AI aircraft: the one with more damage gets the kill, the other an assist."""
    sc = _scenario()
    sc.fly(500, 501, 3)  # C: same side as A
    sc.damage(100, 100, 301, 0.3)
    sc.damage(100.5, 500, 301, 0.7)
    sc.kill(101, 500, 301)
    sc.end(200, 100, 101)
    sc.end(200, 500, 501)
    result = sc.result()
    a, c = by_acct(result, 1), by_acct(result, 3)
    assert (a.friendly_kills, c.friendly_kills) == (0, 1)
    assert a.friendly_damage == pytest.approx(0.3)
    assert c.friendly_damage == pytest.approx(0.7)


def test_fire_after_the_sortie_ended_is_not_counted() -> None:
    sc = _scenario()
    sc.end(100, 100, 101)
    sc.damage(150, 100, 301, 0.5)
    sc.hit(150, 100, 301)
    a = by_acct(sc.result(), 1)
    assert (a.friendly_hits, a.friendly_damage) == (0, 0.0)
