"""Ground losses (design_doc/13_game_rules.md, OQ-32): `taxi_accident` and `strafed_on_ground`.

They only label losses that already count as deaths and lost planes (option (a): a crash before takeoff still counts).
- taxi accident = lost, the aircraft never took off before the loss, `loss_cause` self (an air start never qualifies);
- strafed on the ground = lost to an attacker while on the ground: never took off, or landed and not airborne since,
  with every attacker hit or damage coming after that landing (a shot-up aircraft that crash-lands was shot down);
- gunners are never flagged.

Ids: player A (aircraft 100, bot 101), enemy B (aircraft 200, bot 201), see `builder.py`.
"""

from il2ks.core.replay.result import MissionResult, SortieResult
from tests.unit.replay.builder import FAR, GROUND, NO, Scenario, by_acct


def _flags(s: SortieResult) -> tuple[bool, bool]:
    return s.taxi_accident, s.strafed_on_ground


def _parked_a_destroyed(attacker: int, *, with_hits: bool = False) -> MissionResult:
    """A never takes off. `attacker` (an object id, or NO for the environment) destroys the parked aircraft."""
    sc = Scenario()
    sc.fly_b()
    sc.player(0, 100, 101, 1)
    if attacker != NO:
        sc.damage(29, attacker, 100, 0.6, pos=GROUND)
        if with_hits:
            sc.hit(29, attacker, 100)
    sc.kill(30, attacker, 100, pos=GROUND)
    sc.end(30.1, 100, 101, GROUND)
    return sc.result()


# --- taxi accidents -------------------------------------------------------------------------------------------------


def test_crash_before_takeoff_with_no_attacker_is_a_taxi_accident() -> None:
    """Option (a): it stays a death and a lost plane; the flag is only a label."""
    a = by_acct(_parked_a_destroyed(NO), 1)
    assert _flags(a) == (True, False)
    assert (a.outcome, a.is_plane_lost, a.is_death, a.loss_cause) == ("crashed", True, True, "self")
    destroyed = next(e for e in a.timeline if e.kind == "destroyed")
    assert destroyed.detail == "taxi_accident"


def test_crash_after_takeoff_is_not_a_taxi_accident() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.kill(60, NO, 100, pos=FAR)
    sc.end(60.1, 100, 101, FAR)
    a = by_acct(sc.result(), 1)
    assert (a.outcome, a.loss_cause) == ("crashed", "self")
    assert _flags(a) == (False, False)
    assert next(e for e in a.timeline if e.kind == "destroyed").detail == ""


def test_air_start_crash_is_not_a_taxi_accident() -> None:
    sc = Scenario()
    sc.player(0, 100, 101, 1, in_air=0, pos=FAR)
    sc.kill(10, NO, 100, pos=FAR)
    sc.end(10.1, 100, 101, FAR)
    a = by_acct(sc.result(), 1)
    assert (a.outcome, a.loss_cause, a.takeoffs) == ("crashed", "self", 0)
    assert _flags(a) == (False, False)


def test_landed_then_destroyed_by_the_environment_is_not_a_taxi_accident() -> None:
    """It did take off earlier in the sortie, so it isn't "before the first takeoff"."""
    sc = Scenario()
    sc.fly_a()
    sc.land(100, 100)
    sc.kill(120, NO, 100, pos=GROUND)
    sc.end(121, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.outcome, a.loss_cause) == ("crashed", "self")
    assert _flags(a) == (False, False)


def test_disconnect_death_on_the_ground_without_an_attacker_is_a_taxi_accident() -> None:
    """FR-ING-21 already makes a disconnect after damage a death and a loss; with no takeoff and no attacker it is
    labelled like any other crash before takeoff."""
    sc = Scenario()
    sc.player(0, 100, 101, 1)
    sc.damage(20, NO, 100, 0.4, pos=GROUND)
    sc.disconnect(25, 1)
    sc.remove_bot(25, 101, GROUND)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.is_death, a.is_plane_lost, a.loss_cause) == ("disconnected", True, True, "self")
    assert _flags(a) == (True, False)


def test_disconnect_on_the_ground_without_damage_is_no_loss_and_no_flag() -> None:
    sc = Scenario()
    sc.player(0, 100, 101, 1)
    sc.disconnect(25, 1)
    sc.remove_bot(25, 101, GROUND)
    a = by_acct(sc.result(), 1)
    assert (a.is_death, a.is_plane_lost) == (False, False)
    assert _flags(a) == (False, False)


def test_gunners_are_never_flagged() -> None:
    sc = Scenario()
    sc.player(0, 200, 201, 2, aircraft_type="IL-10", country=501)
    sc.player(1, 500, 501, 3, aircraft_type="Turret_IL10", country=501, parent=200)
    sc.kill(20, NO, 200, pos=GROUND)
    sc.end(20.1, 500, 501, GROUND)
    sc.end(20.1, 200, 201, GROUND)
    result = sc.result()
    pilot, gunner = by_acct(result, 2), by_acct(result, 3)
    assert (gunner.role, gunner.is_plane_lost) == ("gunner", True)
    assert _flags(gunner) == (False, False)
    assert _flags(pilot) == (True, False)


# --- strafed on the ground ---------------------------------------------------------------------------------------


def test_parked_aircraft_destroyed_by_an_attacker_before_takeoff_is_strafed() -> None:
    result = _parked_a_destroyed(200, with_hits=True)
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert _flags(a) == (False, True)
    assert (a.outcome, a.is_plane_lost, a.is_death, a.loss_cause) == ("shot_down", True, True, "attacker")
    assert next(e for e in a.timeline if e.kind == "shot_down").detail == "strafed"
    assert b.kills_air == 1  # the kill credit is unchanged


def test_kill_line_naming_an_attacker_is_enough_before_takeoff() -> None:
    """No hit or damage lines (the log can omit them): the kill line alone names the attacker."""
    sc = Scenario()
    sc.fly_b()
    sc.player(0, 100, 101, 1)
    sc.kill(30, 200, 100, pos=GROUND)
    sc.end(30.1, 100, 101, GROUND)
    assert _flags(by_acct(sc.result(), 1)) == (False, True)


def test_landed_aircraft_destroyed_by_an_attacker_is_strafed() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.land(100, 100)
    sc.hit(120, 200, 100)
    sc.damage(120, 200, 100, 0.5, pos=GROUND)
    sc.kill(125, 200, 100, pos=GROUND)
    sc.end(125.1, 100, 101)
    a = by_acct(sc.result(), 1)
    assert _flags(a) == (False, True)
    assert (a.outcome, a.loss_cause, a.takeoffs, a.landings) == ("shot_down", "attacker", 1, 1)


def test_crash_landing_after_being_shot_up_is_shot_down_not_strafed() -> None:
    """The attacker's damage came before the landing: the aircraft was shot down and then destroyed on the ground."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.hit(80, 200, 100)
    sc.damage(80, 200, 100, 0.6)
    sc.land(100, 100)
    sc.damage(110, 200, 100, 0.2, pos=GROUND)  # more fire on the ground, but the first hit was in the air
    sc.kill(115, 200, 100, pos=GROUND)
    sc.end(115.1, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.outcome, a.loss_cause) == ("shot_down", "attacker")
    assert _flags(a) == (False, False)


def test_a_hit_on_the_pilot_before_landing_also_counts_as_shot_up() -> None:
    """Crew count with the aircraft: the pilot bot was hit in the air."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.hit(80, 200, 101)
    sc.land(100, 100)
    sc.kill(120, 200, 100, pos=GROUND)
    sc.end(120.1, 100, 101)
    assert _flags(by_acct(sc.result(), 1)) == (False, False)


def test_shot_up_then_landed_then_destroyed_by_the_environment_is_not_flagged() -> None:
    """A earlier attacker hit makes the loss an attacker loss, but the destruction is after the landing; the
    environment finishing the job isn't strafing and the hit came before landing."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(80, 200, 100, 0.6)
    sc.land(100, 100)
    sc.kill(120, NO, 100, pos=GROUND)
    sc.end(120.1, 100, 101)
    a = by_acct(sc.result(), 1)
    assert a.loss_cause == "attacker"
    assert _flags(a) == (False, False)


def test_destroyed_in_the_air_is_not_strafed() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(60, 200, 100, 0.9)
    sc.kill(60.5, 200, 100)
    sc.end(60.6, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.outcome, a.loss_cause) == ("shot_down", "attacker")
    assert _flags(a) == (False, False)


def test_airborne_again_after_a_landing_is_not_strafed() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.land(60, 100)
    sc.takeoff(100, 100)
    sc.damage(150, 200, 100, 0.9)
    sc.kill(151, 200, 100)
    sc.end(151.1, 100, 101)
    a = by_acct(sc.result(), 1)
    assert _flags(a) == (False, False)


def test_air_start_shot_down_is_not_strafed() -> None:
    sc = Scenario()
    sc.fly_b()
    sc.player(0, 100, 101, 1, in_air=0, pos=FAR)
    sc.damage(10, 200, 100, 0.9)
    sc.kill(10.5, 200, 100)
    sc.end(10.6, 100, 101)
    assert _flags(by_acct(sc.result(), 1)) == (False, False)


def test_strafed_flag_is_independent_of_the_post_end_window() -> None:
    """The kill line comes 3 s after the sortie end (inside the 5 s ground window): still a ground loss."""
    sc = Scenario()
    sc.fly_b()
    sc.player(0, 100, 101, 1)
    sc.damage(29, 200, 100, 0.9, pos=GROUND)
    sc.end(30, 100, 101, GROUND)
    sc.kill(33, 200, 100, pos=GROUND)
    a = by_acct(sc.result(), 1)
    assert (a.is_plane_lost, _flags(a)) == (True, (False, True))
