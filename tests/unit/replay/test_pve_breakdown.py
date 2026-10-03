"""Replay scenarios for the PvE breakdown (FR-WEB-21, design_doc/13_game_rules.md, "PvE breakdown"): the class of what
caused a sortie's loss, and the air kills split by victim (player vs AI).

Ids: A (acct 1, aircraft 100, F-86A-5, country 601, coalition 2) is the victim; B (acct 2, aircraft 200, MiG-15bis,
country 501, coalition 1) is an enemy player. Attackers added by `_with`: 301 (AI MiG-15bis, 501), 302 (AI B-29, 501),
303 (AI flak, 501), 304 (AI tank, 501), 305 (AI F-86A-5, 601, A's own side), 306 (an uncatalogued type, 501).
"""

import pytest

from il2ks.core.logparse.events import Pos
from il2ks.core.replay.result import LossClass
from tests.unit.replay.builder import NO, Scenario, by_acct

CHUTE = Pos(10_500.0, 1_500.0, 10_000.0)  # far from where the aircraft was lost, like test_pilot_fate


def _with() -> Scenario:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.declare(0, 301, "MiG-15bis", 501)
    sc.declare(0, 302, "B-29", 501)
    sc.declare(0, 303, "Flak 37", 501)
    sc.declare(0, 304, "M46 Patton", 501)
    sc.declare(0, 305, "F-86A-5", 601)
    sc.declare(0, 306, "Mystery object", 501)
    return sc


def _shot_down_by(attacker: int) -> Scenario:
    sc = _with()
    sc.damage(100, attacker, 100, 1.0)
    sc.kill(101, attacker, 100)
    sc.end(200, 100, 101)
    return sc


@pytest.mark.parametrize(
    ("attacker", "expected"),
    [
        (200, "player"),
        (301, "ai_aircraft"),
        (302, "ai_gunner"),
        (303, "aaa"),
        (304, "ground"),
        (305, "friendly"),
        (306, "unknown"),
    ],
)
def test_death_class_follows_the_killer(attacker: int, expected: LossClass) -> None:
    a = by_acct(_shot_down_by(attacker).result(), 1)
    assert (a.is_death, a.is_plane_lost, a.loss_cause) == (True, True, "attacker")
    assert a.loss_class == expected


def test_a_friendly_player_is_friendly_not_player() -> None:
    sc = _with()
    sc.fly(600, 601, 4)  # D, A's side
    sc.damage(100, 600, 100, 1.0)
    sc.kill(101, 600, 100)
    sc.end(200, 100, 101)
    assert by_acct(sc.result(), 1).loss_class == "friendly"


def test_no_attacker_is_environment() -> None:
    sc = _with()
    sc.kill(100, NO, 100)  # crash: AID:-1, no damage from anybody
    sc.end(200, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.is_death, a.loss_cause, a.loss_class) == (True, "self", "environment")


def test_the_most_damage_wins_when_the_kill_line_names_nobody() -> None:
    sc = _with()
    sc.damage(99, 200, 100, 0.3)
    sc.damage(100, 303, 100, 0.6)
    sc.kill(101, NO, 100)
    sc.end(200, 100, 101)
    assert by_acct(sc.result(), 1).loss_class == "aaa"


def test_hits_without_damage_still_name_the_class() -> None:
    """45 of 5,842 losses in the samples: attacker hits but no attacker damage, so credit_kill names nobody."""
    sc = _with()
    sc.hit(99, 303, 100)
    sc.hit(99.5, 303, 100)
    sc.hit(99.7, 200, 100)
    sc.kill(101, NO, 100)
    sc.end(200, 100, 101)
    a = by_acct(sc.result(), 1)
    assert a.loss_cause == "attacker"
    assert a.loss_class == "aaa"


def test_bailout_after_flak_damage_is_a_plane_lost_to_aaa_without_a_death() -> None:
    sc = _with()
    sc.damage(100, 303, 100, 0.3)
    sc.end(110, 0, 101)
    sc.end(110, 0, 101)
    sc.remove_bot(110, 101, CHUTE)
    sc.kill(110.5, NO, 100)
    a = by_acct(sc.result(), 1)
    assert (a.is_death, a.is_plane_lost, a.loss_class) == (False, True, "aaa")


def test_no_loss_no_class() -> None:
    sc = _with()
    sc.damage(100, 303, 100, 0.1)  # hurt but flew on
    sc.land(150, 100)
    sc.end(160, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.is_plane_lost, a.loss_class) == (False, None)


def test_air_kills_split_by_victim() -> None:
    sc = _with()
    sc.damage(100, 100, 301, 1.0)
    sc.kill(101, 100, 301)  # an AI aircraft
    sc.damage(110, 100, 200, 1.0)
    sc.kill(111, 100, 200)  # a player
    sc.damage(120, 100, 305, 1.0)
    sc.kill(121, 100, 305)  # an AI aircraft on A's own side: friendly fire, not a kill
    sc.damage(130, 100, 304, 1.0)
    sc.kill(131, 100, 304)  # a ground kill
    sc.end(200, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.kills_air, a.kills_air_pvp, a.kills_air_ai, a.kills_ground) == (2, 1, 1, 1)
    assert a.kills_air_pvp + a.kills_air_ai == a.kills_air
