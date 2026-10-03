"""Replay scenario tests: bailout rule v2 (FR-ING-14), self-destruction (FR-ING-17), disconnects (FR-ING-21, -22)."""

import pytest

from il2ks.core.logparse.events import Pos, WheelsOnEvent
from il2ks.core.replay.result import MissionResult
from tests.unit.replay.builder import FAR, GROUND, NO, Scenario, by_acct, ids

CHUTE = Pos(FAR.x + 500, FAR.y - 1500, FAR.z)  # the pilot's final position under the parachute, 500 m+ away


def _bailout(sc: Scenario, *, at: float = 110, aircraft_destroyed_after: float | None = 0.5) -> None:
    """The bailout shape: AType 4 PLID:0 (twice), pilot removed far away, the abandoned aircraft destroyed by AID:-1."""
    sc.end(at, 0, 101)
    sc.end(at, 0, 101)
    sc.remove_bot(at, 101, CHUTE)
    if aircraft_destroyed_after is not None:
        sc.kill(at + aircraft_destroyed_after, NO, 100)


def test_bailout_after_attack_credits_the_attacker() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(100, 200, 100, 0.3)
    _bailout(sc)
    result = sc.result()
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.pilot_fate, a.pilot_fate_source) == ("bailed_out", "inferred")
    assert not a.suspected_early_bailout
    assert (a.loss_cause, a.is_plane_lost, a.is_death) == ("attacker", True, False)
    assert a.pilot_status == "healthy"
    (kill,) = result.kills
    assert (kill.killer_sortie_index, kill.via, kill.credit) == (b.index, "abandoned_aircraft", "kill")
    assert b.kills_air == 1
    assert "bailout" in [e.kind for e in a.timeline]


def test_abandoned_aircraft_destroyed_long_after_the_sortie_end_is_still_credited() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(100, 200, 100, 0.3)
    _bailout(sc, aircraft_destroyed_after=60)
    result = sc.result()
    assert by_acct(result, 2).kills_air == 1
    assert by_acct(result, 1).aircraft_status == "destroyed"


def test_suspected_early_bailout_without_attacker_damage() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.damage(109.9, NO, 100, 0.05)  # the abandoned aircraft's own damage tick doesn't count as an attack
    _bailout(sc)
    a = by_acct(sc.result(), 1)
    assert a.pilot_fate == "bailed_out"
    assert a.suspected_early_bailout
    assert a.loss_cause == "self"
    assert a.is_plane_lost


def test_bailout_that_follows_a_hit_is_not_suspected() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.hit(100, 200, 100)  # a hit without damage still counts as an attack (rule v2, condition 5)
    _bailout(sc)
    assert not by_acct(sc.result(), 1).suspected_early_bailout


def test_bailout_right_before_mission_end_is_not_suspected() -> None:
    sc = Scenario()
    sc.fly_a()
    _bailout(sc, at=110)
    sc.mission_end(150)
    assert not by_acct(sc.result(), 1).suspected_early_bailout


def test_bailout_with_a_disconnect_is_not_suspected() -> None:
    sc = Scenario()
    sc.fly_a()
    _bailout(sc, at=110)
    sc.disconnect(112, 1)
    a = by_acct(sc.result(), 1)
    assert a.pilot_fate == "bailed_out"
    assert not a.suspected_early_bailout
    assert a.disconnected


def test_pilot_dying_with_the_aircraft_is_not_a_bailout() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(100, 200, 100, 1.0)
    sc.kill(100, 200, 100)
    sc.kill(100.2, 200, 101)  # the pilot, within 0.5 s of the aircraft
    sc.end(100.5, 0, 101)
    sc.remove_bot(100.5, 101, CHUTE)
    a = by_acct(sc.result(), 1)
    assert a.pilot_fate == "in_aircraft"
    assert (a.pilot_status, a.is_death) == ("dead", True)
    assert a.outcome == "shot_down"


def test_pilot_exiting_on_the_ground_is_no_bailout() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.land(600, 100)
    sc.end(700, 0, 101)
    sc.remove_bot(700, 101, Pos(GROUND.x + 10, GROUND.y, GROUND.z))
    a = by_acct(sc.result(), 1)
    assert a.pilot_fate == "exited_on_ground"
    assert a.outcome == "landed"
    assert (a.is_plane_lost, a.is_death, a.suspected_early_bailout) == (False, False, False)


def test_crash_landing_with_the_pilot_next_to_the_wreck_is_no_bailout() -> None:
    """Doc 12: the aircraft is destroyed by AID:-1 with its wheels still logged up, then the pilot climbs out."""
    sc = Scenario()
    sc.fly_a()
    sc.kill(300, NO, 100, pos=GROUND)
    sc.end(305, 0, 101)
    sc.remove_bot(305, 101, Pos(GROUND.x + 30, GROUND.y, GROUND.z))
    a = by_acct(sc.result(), 1)
    assert a.pilot_fate == "exited_on_ground"
    assert not a.suspected_early_bailout
    assert (a.outcome, a.loss_cause, a.is_plane_lost) == ("crashed", "self", True)
    assert not a.is_death


def test_bailout_distance_threshold_is_a_config_value() -> None:
    from il2ks.core.replay.config import ReplayRules

    sc = Scenario()
    sc.fly_a()
    sc.end(110, 0, 101)
    sc.remove_bot(110, 101, Pos(FAR.x + 150, FAR.y, FAR.z))
    sc.kill(110.5, NO, 100)
    assert by_acct(sc.result(), 1).pilot_fate == "bailed_out"
    assert by_acct(sc.result(ReplayRules(bailout_min_distance_m=200.0)), 1).pilot_fate == "exited_on_ground"


# --- structural failure (FR-ING-17 definition v2) ------------------------------------------------------------------


def _structural(self_damage_before: float, ground_contact_after: float | None) -> MissionResult:
    sc = Scenario()
    sc.fly_a()
    sc.damage(100 - self_damage_before, NO, 100, 0.5)
    sc.kill(100, NO, 100)
    sc.end(100.1, 100, 101)
    if ground_contact_after is not None:
        sc.add(WheelsOnEvent(tick=round((100 + ground_contact_after) * 50), object_id=ids(100), pos=GROUND))
    return sc.result()


def test_structural_failure_is_flagged() -> None:
    a = by_acct(_structural(0.5, 8.0), 1)
    assert (a.loss_cause, a.suspected_structural_failure) == ("self", True)


@pytest.mark.parametrize(
    ("before", "contact"),
    [
        (5.0, 8.0),  # self damage began long before: not sudden
        (0.5, 0.4),  # the wreck hit the ground within 1 s: terrain impact
        (0.5, None),  # no ground contact logged: can't classify
    ],
)
def test_not_structural_failure(before: float, contact: float | None) -> None:
    assert not by_acct(_structural(before, contact), 1).suspected_structural_failure


def test_attacker_loss_is_never_structural() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(99.7, 200, 100, 0.5)
    sc.kill(100, NO, 100)
    sc.add(WheelsOnEvent(tick=108 * 50, object_id=ids(100), pos=GROUND))
    sc.end(100.1, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.loss_cause, a.suspected_structural_failure) == ("attacker", False)


# --- disconnects ---------------------------------------------------------------------------------------------------


def _disconnect(damage_at: float | None, attacker: int = NO, *, disconnect_at: float = 130) -> MissionResult:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    if damage_at is not None:
        sc.damage(damage_at, attacker, 100, 0.3)
    sc.disconnect(disconnect_at, 1)
    sc.remove_bot(disconnect_at + 1, 101, FAR)  # no AType 4 at all
    return sc.result()


def test_disconnect_after_recent_self_damage_is_a_death() -> None:
    a = by_acct(_disconnect(120), 1)
    assert a.pilot_fate == "disconnected"
    assert (a.is_death, a.is_plane_lost, a.loss_cause, a.disconnected) == (True, True, "self", True)
    assert a.pilot_status == "dead"


def test_disconnect_without_damage_is_not_a_death() -> None:
    a = by_acct(_disconnect(None), 1)
    assert a.pilot_fate == "disconnected"
    assert (a.is_death, a.is_plane_lost) == (False, False)
    assert a.loss_cause == "none"
    assert a.disconnected


def test_disconnect_with_old_damage_is_not_a_death() -> None:
    a = by_acct(_disconnect(damage_at=10, disconnect_at=300), 1)  # 290 s before: outside the 120 s window
    assert not a.is_death


def test_disconnect_after_attacker_damage_credits_the_attacker() -> None:
    result = _disconnect(120, attacker=200)
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.is_death, a.loss_cause) == (True, "attacker")
    assert a.outcome == "shot_down"
    (kill,) = result.kills
    assert (kill.killer_sortie_index, kill.via) == (b.index, "disconnect")
    assert b.kills_air == 1


def test_disconnect_with_a_plain_sortie_end_while_airborne() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.end(200, 100, 101, pos=FAR)
    sc.disconnect(205, 1)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.disconnected, a.is_death) == ("disconnected", True, False)


def test_landed_player_leaving_afterwards_stays_in_aircraft() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.land(100, 100)
    sc.end(110, 100, 101)
    sc.disconnect(115, 1)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.outcome, a.disconnected) == ("in_aircraft", "landed", True)
    assert not a.is_death


# --- disconnect fate with an attacker's kill (FR-ING-21) -------------------------------------------------------


def test_disconnect_fate_holds_when_an_attacker_destroyed_the_aircraft() -> None:
    """No AType 4: the fate is `disconnected` even though an enemy destroyed the aircraft (like il2_stats). Death, loss,
    cause and credit stay as before (FR-ING-21, -22): the kill line alone is enough, no recent damage line needed."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.kill(101, 200, 100)
    sc.disconnect(101.5, 1)
    sc.remove_bot(101.6, 101, FAR)
    result = sc.result()
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.pilot_fate, a.pilot_fate_source, a.disconnected) == ("disconnected", "inferred", True)
    assert (a.is_death, a.pilot_status, a.is_plane_lost, a.loss_cause) == (True, "dead", True, "attacker")
    assert (a.outcome, a.aircraft_status) == ("shot_down", "destroyed")
    (kill,) = result.kills
    assert (kill.killer_sortie_index, kill.via, kill.credit) == (b.index, "direct", "kill")
    assert b.kills_air == 1
    assert "disconnect" in [e.kind for e in a.timeline]


def test_disconnect_fate_holds_for_a_plain_end_shot_down_shape() -> None:
    """AType 4 with the aircraft id, an AType 21 within 30 s, and the aircraft destroyed by an attacker right after."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(99.5, 200, 100, 0.5)
    sc.end(100, 100, 101)
    sc.kill(101, 200, 100)
    sc.disconnect(105, 1)
    result = sc.result()
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.pilot_fate, a.pilot_fate_source) == ("disconnected", "inferred")
    assert (a.is_death, a.is_plane_lost, a.loss_cause, a.outcome) == (True, True, "attacker", "shot_down")
    assert b.kills_air == 1


def test_pilot_killed_in_a_surviving_aircraft_gets_a_killed_entry() -> None:
    """Only the pilot bot gets an AType 3: the timeline has a `killed` entry naming the attacker, no `shot_down`."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(100, 200, 101, 1.0)
    sc.kill(100.2, 200, 101)
    sc.end(100.5, 100, 101)
    result = sc.result()
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.is_death, a.loss_cause) == (True, "attacker")
    assert "shot_down" not in [e.kind for e in a.timeline]
    (killed,) = [e for e in a.timeline if e.kind == "killed"]
    assert killed.counterpart is not None
    assert (killed.counterpart.object_type, killed.counterpart.sortie_index) == ("MiG-15bis", b.index)


def test_killed_pilot_stays_in_aircraft_even_without_a_sortie_end() -> None:
    """The pilot bot's own AType 3 means the pilot died: that isn't a disconnect, whatever the log does next."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(100, 200, 100, 1.0)
    sc.kill(100, 200, 100)
    sc.kill(100.1, 200, 101)
    sc.remove_bot(100.2, 101, FAR)  # no AType 4
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.pilot_fate_source) == ("in_aircraft", "event")
    assert (a.is_death, a.pilot_status, a.loss_cause) == (True, "dead", "attacker")
