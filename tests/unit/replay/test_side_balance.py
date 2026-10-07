"""Side balance (OQ-134): pilots spawned in per side weighted by time, and the underdog sortie.

Scenario (seconds): BLUFOR A flies 0-100, REDFOR B flies 0-100, REDFOR C flies 50-100; the mission ends at 100.
REDFOR has 1 pilot for 50 s and 2 for 50 s (average 1.5), BLUFOR has 1 (average 1.0).
"""

from il2ks.core.replay.result import MissionResult
from tests.unit.replay.builder import Scenario, by_acct


def _three_pilots() -> MissionResult:
    sc = Scenario()
    sc.fly(100, 101, 1)  # acct 1: F-86A-5, country 601 (BLUFOR)
    sc.fly(200, 201, 2, aircraft_type="MiG-15bis", country=501)
    sc.fly(300, 301, 3, aircraft_type="MiG-15bis", country=501, spawn=50, up=55)
    sc.end(100, 100, 101)
    sc.end(100, 200, 201)
    sc.end(100, 300, 301)
    sc.mission_end(100)
    return sc.result()


def test_mission_average_is_time_weighted() -> None:
    info = _three_pilots().mission
    assert (info.redfor_players, info.blufor_players) == (1.5, 1.0)


def test_underdog_is_judged_over_the_pilots_own_time() -> None:
    result = _three_pilots()
    assert by_acct(result, 1).underdog  # BLUFOR 1.0 against REDFOR 1.5 over its 100 s
    assert not by_acct(result, 2).underdog
    assert not by_acct(result, 3).underdog


def test_equal_strength_is_not_underdog() -> None:
    sc = Scenario()
    sc.fly(100, 101, 1)
    sc.fly(200, 201, 2, aircraft_type="MiG-15bis", country=501)
    sc.end(100, 100, 101)
    sc.end(100, 200, 201)
    sc.mission_end(100)
    result = sc.result()
    assert not by_acct(result, 1).underdog
    assert not by_acct(result, 2).underdog
    assert (result.mission.redfor_players, result.mission.blufor_players) == (1.0, 1.0)


def test_joining_late_is_weighted_by_how_long_the_side_was_smaller() -> None:
    """A (BLUFOR) flies 0-100 against B (REDFOR, 0-100) and C (REDFOR, 50-100); D (BLUFOR) joins at 80.
    A: BLUFOR 120 pilot-seconds against REDFOR 150, underdog. D flew 80-100 with 2 against 2: not underdog."""
    sc = Scenario()
    sc.fly(100, 101, 1)
    sc.fly(200, 201, 2, aircraft_type="MiG-15bis", country=501)
    sc.fly(300, 301, 3, aircraft_type="MiG-15bis", country=501, spawn=50, up=55)
    sc.fly(400, 401, 4, spawn=80, up=85)
    for aircraft in (100, 200, 300, 400):
        sc.end(100, aircraft, aircraft + 1)
    sc.mission_end(100)
    result = sc.result()
    assert by_acct(result, 1).underdog
    assert not by_acct(result, 4).underdog  # equal while it flew


def test_a_gunner_takes_the_pilots_verdict() -> None:
    sc = Scenario()
    sc.fly(100, 101, 1)
    sc.player(0, 500, 501, 4, aircraft_type="Turret_IL10", country=601, parent=100)
    sc.fly(200, 201, 2, aircraft_type="MiG-15bis", country=501)
    sc.fly(300, 301, 3, aircraft_type="MiG-15bis", country=501)
    sc.end(100, 500, 501)
    sc.end(100, 100, 101)
    sc.end(100, 200, 201)
    sc.end(100, 300, 301)
    sc.mission_end(100)
    result = sc.result()
    assert by_acct(result, 1).underdog  # 1 pilot against 2: the gunner adds nobody
    assert by_acct(result, 4).underdog
    assert (result.mission.redfor_players, result.mission.blufor_players) == (2.0, 1.0)
