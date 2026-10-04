"""Per-sortie facts the achievements read (doc 17): rams, the first blood of a mission, the best burst of air kills.

A (acct 1, aircraft 100, F-86A-5, coalition 2) shoots; B (acct 2, aircraft 200, MiG-15bis) is the enemy player."""

from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.toggles import RuleToggles
from tests.unit.replay.builder import FAR, NO, Pos, Scenario, by_acct

BESIDE = Pos(FAR.x + 6.0, FAR.y, FAR.z)


def _shoot(sc: Scenario, t: float, shooter: int, victim: int) -> None:
    sc.damage(t, shooter, victim, 1.0)
    sc.kill(t + 1, shooter, victim)


def _ai_targets(sc: Scenario, n: int) -> None:
    for i in range(n):
        sc.declare(0, 300 + i, "MiG-15bis", 501)


def test_a_ram_is_counted_for_both_pilots_and_only_when_credited() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.kill(110, NO, 100, pos=FAR)
    sc.kill(110.04, NO, 200, pos=BESIDE)
    sc.end(110.2, 100, 101)
    sc.end(110.2, 200, 201)

    result = sc.result()
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.rams, b.rams) == (1, 1)
    assert (a.kills_air, b.kills_air) == (1, 1)
    assert all(k.ram for k in result.kills if k.credit == "kill")

    off = sc.result(ReplayRules(toggles=RuleToggles(credit_rams=False)))
    assert (by_acct(off, 1).rams, by_acct(off, 2).rams) == (0, 0)


def test_a_shot_down_aircraft_is_no_ram() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    _shoot(sc, 100, 100, 200)
    sc.end(200, 100, 101)

    a = by_acct(sc.result(), 1)
    assert (a.kills_air, a.rams) == (1, 0)


def test_first_blood_is_the_first_pvp_air_kill_of_the_mission() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.fly(500, 501, 3, aircraft_type="MiG-15bis", country=501)
    _ai_targets(sc, 1)
    _shoot(sc, 50, 100, 300)  # an AI aircraft first: A does not get the first blood for it
    _shoot(sc, 100, 500, 100)  # C shoots A down: the first PvP kill
    _shoot(sc, 120, 200, 500)  # a later kill of B's, on C
    sc.end(300, 200, 201)

    result = sc.result()
    flags = {acct: by_acct(result, acct).first_blood for acct in (1, 2, 3)}
    assert flags == {1: False, 2: False, 3: True}


def test_no_first_blood_without_a_pvp_kill() -> None:
    sc = Scenario()
    sc.fly_a()
    _ai_targets(sc, 2)
    _shoot(sc, 50, 100, 300)
    sc.end(300, 100, 101)

    assert not by_acct(sc.result(), 1).first_blood


def test_friendly_fire_is_no_first_blood() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly(500, 501, 3)  # a second F-86A of A's coalition
    _shoot(sc, 100, 100, 500)
    sc.end(300, 100, 101)

    result = sc.result()
    assert not any(by_acct(result, acct).first_blood for acct in (1, 3))


def test_multi_kill_is_the_best_burst_within_the_window() -> None:
    sc = Scenario()
    sc.fly_a()
    _ai_targets(sc, 5)
    for i, t in enumerate((100, 110, 125, 400, 520)):  # three kills in 25 s, then two lone kills
        _shoot(sc, t, 100, 300 + i)
    sc.end(600, 100, 101)

    a = by_acct(sc.result(), 1)
    assert (a.kills_air, a.multi_kill) == (5, 3)


def test_multi_kill_of_a_sortie_without_kills_is_zero() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.end(100, 100, 101)

    a = by_acct(sc.result(), 1)
    assert (a.multi_kill, a.rams, a.first_blood) == (0, 0, False)
