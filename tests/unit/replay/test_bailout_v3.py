"""Bailout rule v3 (FR-ING-14): v2 plus the ejection spawn (Rufus's method 1) and the "pilot not already dead" gate
(Rufus's method 2). One scenario per case type found while evaluating both methods on the 210 sample missions."""

from il2ks.core.logparse.events import Pos, WheelsOnEvent
from il2ks.core.logparse.files import MissionLog, parse_mission
from il2ks.core.logparse.parser import ParseStats
from il2ks.core.replay.result import SortieResult
from il2ks.core.replay.state import run
from tests.conftest import FIXTURE_LOGS
from tests.unit.replay.builder import FAR, GROUND, NO, FakeCatalog, Scenario, by_acct, tick

NEAR_WRECK = Pos(FAR.x + 40, FAR.y, FAR.z)  # inside the 100 m of rule v2


def _eject(sc: Scenario, t: float, *, parent: int = -1, pos: Pos = FAR) -> None:
    """The pilot body AType 12 for bot 101: `PID:-1` is an ejection, `PID:100` a seated bot re-announced."""
    sc.declare(t, 101, "BotPlanePilot_Test", 601, parent=parent, pos=pos)


def test_ejection_spawn_is_a_bailout_even_within_100_m_of_the_wreck() -> None:
    """A pilot who bails and quickly ends the mission is torn down near the aircraft's frozen logged position."""
    sc = Scenario()
    sc.fly_a()
    sc.kill(110, NO, 100, pos=FAR)
    _eject(sc, 110.2, pos=NEAR_WRECK)
    sc.end(110.2, 0, 101)
    sc.remove_bot(110.5, 101, NEAR_WRECK)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.pilot_fate_source) == ("bailed_out", "event")
    assert a.is_plane_lost


def test_ejection_spawn_beats_a_stale_wheels_flag() -> None:
    """Wheels logged on without an AType 6, so the live airborne flag says "on the ground" while the aircraft is in the
    air (mission 2026-09-19_22-34-13, tick 248690). Rule v2 reads that as a ground exit; the ejection spawn doesn't."""
    sc = Scenario()
    sc.fly_a()
    sc.add(WheelsOnEvent(tick=tick(60), object_id=sc.events[1].object_id, pos=GROUND))  # type: ignore[attr-defined]
    sc.kill(110, NO, 100, pos=FAR)
    _eject(sc, 200)
    sc.end(200, 0, 101)
    sc.remove_bot(200.1, 101, Pos(FAR.x + 400, FAR.y, FAR.z))
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.pilot_fate_source) == ("bailed_out", "event")


def test_bailout_without_ejection_spawn_stays_inferred() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.end(110, 0, 101)
    sc.remove_bot(110, 101, Pos(FAR.x + 500, FAR.y, FAR.z))
    sc.kill(110.5, NO, 100)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.pilot_fate_source) == ("bailed_out", "inferred")


def test_reannounced_seated_pilot_is_no_ejection() -> None:
    """A seated bot re-announced with `PID:<aircraft>` is no ejection, and 40 m from the aircraft no bailout."""
    sc = Scenario()
    sc.fly_a()
    _eject(sc, 110, parent=100, pos=NEAR_WRECK)
    sc.end(110, 0, 101)
    sc.remove_bot(110.1, 101, NEAR_WRECK)
    sc.kill(110.5, NO, 100, pos=FAR)
    assert by_acct(sc.result(), 1).pilot_fate == "exited_on_ground"


def test_pilot_killed_in_the_seat_is_no_ejection() -> None:
    """A pilot who dies is announced detached at the moment of death (49 such sorties in the samples)."""
    sc = Scenario()
    sc.fly_a()
    sc.kill(110, NO, 100)
    _eject(sc, 111, pos=NEAR_WRECK)
    sc.kill(111, NO, 101, pos=NEAR_WRECK)
    sc.end(111, 0, 101)
    sc.remove_bot(112, 101, NEAR_WRECK)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.pilot_fate_source) == ("in_aircraft", "event")
    assert (a.is_death, a.pilot_status) == (True, "dead")


def test_pilot_dying_a_second_after_the_aircraft_is_not_a_suspected_early_bailout() -> None:
    """Rule v2 flagged a bailout when the pilot died over 0.5 s after the aircraft (PLID:0 is written at the pilot's
    death); 66 of 361 suspected early bailouts in the samples were such pilots."""
    sc = Scenario()
    sc.fly_a()
    sc.kill(110, NO, 100, pos=FAR)
    sc.kill(111.5, NO, 101, pos=Pos(FAR.x + 300, FAR.y, FAR.z))
    sc.end(111.5, 0, 101)
    sc.remove_bot(113, 101, Pos(FAR.x + 300, FAR.y, FAR.z))
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.is_death) == ("in_aircraft", True)
    assert not a.suspected_early_bailout


def test_ejection_before_takeoff_is_a_ground_exit() -> None:
    """The same spawn fires for a pilot who aborts before takeoff: the airborne gate."""
    sc = Scenario()
    sc.player(0, 100, 101, 1)
    _eject(sc, 30, pos=GROUND)
    sc.end(30, 0, 101)
    sc.remove_bot(30.1, 101, GROUND)
    assert by_acct(sc.result(), 1).pilot_fate == "exited_on_ground"


def test_climbing_out_after_landing_is_a_ground_exit() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.land(600, 100)
    _eject(sc, 700, pos=GROUND)
    sc.end(700, 0, 101)
    sc.remove_bot(700.1, 101, Pos(GROUND.x + 10, GROUND.y, GROUND.z))
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.pilot_fate_source) == ("exited_on_ground", "inferred")


def test_crash_landing_walk_away_is_a_ground_exit() -> None:
    """Killed at the landing tick, the pilot gets out 150 m from the logged wreck: rule v2's wheels flag says landed."""
    sc = Scenario()
    sc.fly_a()
    sc.land(300, 100)
    sc.kill(300, NO, 100, pos=GROUND)
    _eject(sc, 330, pos=GROUND)
    sc.end(330, 0, 101)
    sc.remove_bot(330.1, 101, Pos(GROUND.x + 150, GROUND.y, GROUND.z))
    a = by_acct(sc.result(), 1)
    assert a.pilot_fate == "exited_on_ground"
    assert not a.suspected_early_bailout


def test_wreck_landing_after_the_kill_is_still_a_bailout() -> None:
    """A Landing strictly after the kill is the falling wreck hitting the ground; the pilot is already out."""
    sc = Scenario()
    sc.fly_a()
    sc.kill(110, NO, 100, pos=FAR)
    _eject(sc, 110.1, pos=FAR)
    sc.end(110.1, 0, 101)
    sc.remove_bot(110.5, 101, Pos(FAR.x + 300, FAR.y - 200, FAR.z))
    sc.land(125, 100, pos=Pos(FAR.x + 100, GROUND.y, FAR.z))
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.pilot_fate_source) == ("bailed_out", "event")


def test_end_mission_under_the_canopy_right_after_the_mission_end_is_not_suspected_early() -> None:
    sc = Scenario()
    sc.fly_a()
    _eject(sc, 110)
    sc.end(110, 0, 101)
    sc.remove_bot(110.1, 101, Pos(FAR.x + 300, FAR.y, FAR.z))
    sc.mission_end(150)
    a = by_acct(sc.result(), 1)
    assert a.pilot_fate == "bailed_out"
    assert not a.suspected_early_bailout


def test_ejection_then_disconnect_is_a_bailout_but_not_suspected_early() -> None:
    sc = Scenario()
    sc.fly_a()
    _eject(sc, 110)
    sc.end(110, 0, 101)
    sc.remove_bot(110.1, 101, Pos(FAR.x + 300, FAR.y, FAR.z))
    sc.disconnect(112, 1)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.pilot_fate_source) == ("bailed_out", "event")
    assert a.disconnected
    assert not a.suspected_early_bailout


def test_detached_declaration_at_the_mission_end_cleanup_is_no_bailout() -> None:
    """Mission-end teardown also announces bots detached, but the pilot's AType 4 names the aircraft (PLID:100)."""
    sc = Scenario()
    sc.fly_a()
    sc.mission_end(200)
    _eject(sc, 200.1)
    sc.end(200.1, 100, 101)
    sc.remove_bot(200.2, 101, FAR)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.ended_by_mission_end) == ("in_aircraft", True)


def test_ejection_spawn_in_a_real_log_excerpt() -> None:
    """Anonymized excerpt of 2026-09-19_22-34-13 (the stale wheels flag above): the aircraft is destroyed by AID:-1 at
    altitude, the pilot ejects 6 minutes later at 260 m above the airfield 400 m from the wreck."""
    path = FIXTURE_LOGS / "bailout_ejection_stale_wheels.txt.zip"
    log = MissionLog("2026-09-19_22-34-13", "archive", (path,))
    result = run(parse_mission(log, ParseStats()), FakeCatalog())
    (sortie,) = result.sorties
    check(sortie)


def check(sortie: SortieResult) -> None:
    assert (sortie.pilot_fate, sortie.pilot_fate_source) == ("bailed_out", "event")
    assert sortie.is_plane_lost
