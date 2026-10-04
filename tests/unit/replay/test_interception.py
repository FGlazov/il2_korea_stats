"""Interception: air kills of bombers, attackers and transports (doc 13; maintainer request 2026-10-04).

A (acct 1, aircraft 100, F-86A-5) shoots; victims are an AI bomber (302, B-29), an AI fighter (301), the player B
(aircraft 200) once with a guns-only loadout (a fighter) and once with bombs or rockets (an attack sortie), and an AI
IL-10 attacker (307).
"""

import dataclasses

import pytest

from il2ks.core.logparse.events import PlayerSpawnEvent
from il2ks.core.replay.attack import is_interception_victim
from tests.unit.replay.builder import Scenario, by_acct


def _scenario(*, victim_bombs: int, victim_rockets: int) -> Scenario:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.declare(0, 301, "MiG-15bis", 501)
    sc.declare(0, 302, "B-29", 501)
    sc.declare(0, 307, "IL-10", 501)
    for i, event in enumerate(sc.events):
        if isinstance(event, PlayerSpawnEvent) and event.aircraft_id == 200:
            sc.events[i] = dataclasses.replace(event, bombs=victim_bombs, rockets=victim_rockets)
    return sc


def _shoot(sc: Scenario, t: float, victim: int) -> None:
    sc.damage(t, 100, victim, 1.0)
    sc.kill(t + 1, 100, victim)


def test_bombers_and_attackers_count_fighters_do_not() -> None:
    sc = _scenario(victim_bombs=0, victim_rockets=0)
    _shoot(sc, 100, 301)  # an AI fighter
    _shoot(sc, 110, 302)  # an AI bomber
    _shoot(sc, 120, 307)  # an AI attacker
    _shoot(sc, 130, 200)  # a player in a guns-only aircraft
    sc.end(200, 100, 101)

    a = by_acct(sc.result(), 1)

    assert (a.kills_air, a.kills_air_intercept) == (4, 2)


def test_an_ai_transport_counts() -> None:
    """Maintainer, 2026-10-04 (OQ-102): C-47B and Li-2 (paratrooper and supply planes) are interception targets."""
    sc = _scenario(victim_bombs=0, victim_rockets=0)
    sc.declare(0, 310, "C-47B", 501)
    sc.declare(0, 311, "Li-2", 501)
    _shoot(sc, 100, 310)
    _shoot(sc, 110, 311)
    sc.end(200, 100, 101)

    a = by_acct(sc.result(), 1)

    assert (a.kills_air, a.kills_air_intercept) == (2, 2)


@pytest.mark.parametrize(("bombs", "rockets"), [(2, 0), (0, 6)])
def test_a_player_in_an_attack_sortie_counts(bombs: int, rockets: int) -> None:
    sc = _scenario(victim_bombs=bombs, victim_rockets=rockets)
    _shoot(sc, 100, 200)
    sc.end(200, 100, 101)

    a = by_acct(sc.result(), 1)

    assert (a.kills_air, a.kills_air_pvp, a.kills_air_intercept) == (1, 1, 1)


def test_assists_and_ground_kills_do_not_count() -> None:
    sc = _scenario(victim_bombs=0, victim_rockets=0)
    sc.declare(0, 304, "M46 Patton", 501)
    _shoot(sc, 100, 304)
    sc.end(200, 100, 101)

    assert by_acct(sc.result(), 1).kills_air_intercept == 0


@pytest.mark.parametrize(
    ("victim_class", "role", "expected"),
    [
        ("bomber", None, True),
        ("attacker", None, True),
        ("fighter", "attack", True),
        ("fighter", "air_superiority", False),
        ("fighter", None, False),
        ("transport", None, True),
        (None, None, False),
    ],
)
def test_the_rule(victim_class: str | None, role: str | None, expected: bool) -> None:
    assert is_interception_victim(victim_class, role) is expected
