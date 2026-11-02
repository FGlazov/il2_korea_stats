"""Botched landings (doc 13, OQ-144): the pilot wrecked or badly damaged a sound aircraft at an airfield.

Thresholds from the 210 samples: the airfield point is where the aircraft park, and touchdowns lie up to about 2 km from
it; crashes in the first two minutes after takeoff are failed takeoffs; damage around a touchdown is under 15% or 45%+.

Ids: player A (aircraft 100, bot 101), enemy B (aircraft 200, bot 201), see `builder.py`.
"""

import pytest

from il2ks.core.logparse.events import Pos
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import SortieResult
from tests.unit.replay.builder import FAR, GROUND, NO, Scenario, by_acct

FIELD = GROUND
ON_RUNWAY = Pos(GROUND.x + 1_500.0, GROUND.y, GROUND.z)
PAST_THE_RUNWAY = Pos(GROUND.x + 2_500.0, GROUND.y, GROUND.z)
HIGH_ABOVE = Pos(GROUND.x + 500.0, GROUND.y + 800.0, GROUND.z)
TAKEOFF_S = 10.0
FLOWN_S = 600.0


def _airfield_scenario(*, enemy: bool = False) -> Scenario:
    sc = Scenario()
    sc.airfield(1, 601, FIELD)
    if enemy:
        sc.fly_b()
    sc.player(0, 100, 101, 1)
    sc.takeoff(TAKEOFF_S, 100)
    return sc


def _crash(pos: Pos = ON_RUNWAY, at: float = FLOWN_S, *, enemy_damage: float = 0.0) -> SortieResult:
    sc = _airfield_scenario(enemy=bool(enemy_damage))
    if enemy_damage:
        sc.damage(at - 100, 200, 100, enemy_damage, pos=FAR)
    sc.kill(at, NO, 100, pos=pos)
    sc.end(at + 0.1, 100, 101, pos)
    return by_acct(sc.result(), 1)


def _rough_landing(own_damage: float, pos: Pos = ON_RUNWAY, *, enemy_damage: float = 0.0) -> SortieResult:
    """A lands and stops: the aircraft survives with `own_damage` of its own making around the touchdown."""
    sc = _airfield_scenario(enemy=bool(enemy_damage))
    if enemy_damage:
        sc.damage(FLOWN_S - 100, 200, 100, enemy_damage, pos=FAR)
    sc.land(FLOWN_S, 100, pos)
    if own_damage:
        sc.damage(FLOWN_S + 1, NO, 100, own_damage, pos=pos)
    sc.end(FLOWN_S + 60, 100, 101, pos)
    return by_acct(sc.result(), 1)


# --- crashes --------------------------------------------------------------------------------------------------------


def test_a_crash_on_the_runway_of_a_sound_aircraft_is_a_botched_landing() -> None:
    """It stays a crash, a loss and a death: the flag only labels it, like a taxi accident."""
    a = _crash()
    assert a.botched_landing
    assert (a.outcome, a.is_plane_lost, a.loss_cause) == ("crashed", True, "self")
    assert next(e for e in a.timeline if e.kind == "destroyed").detail == "botched_landing"


def test_a_crash_away_from_every_airfield_is_no_botched_landing() -> None:
    assert not _crash(PAST_THE_RUNWAY).botched_landing


def test_the_airfield_may_be_any_sides() -> None:
    """A pilot who wrecks the aircraft on an enemy runway botched that landing too."""
    sc = Scenario()
    sc.airfield(1, 501, FIELD)
    sc.player(0, 100, 101, 1)
    sc.takeoff(TAKEOFF_S, 100)
    sc.kill(FLOWN_S, NO, 100, pos=ON_RUNWAY)
    sc.end(FLOWN_S + 0.1, 100, 101, ON_RUNWAY)
    assert by_acct(sc.result(), 1).botched_landing


def test_a_mission_without_airfields_has_no_botched_landings() -> None:
    sc = Scenario()
    sc.player(0, 100, 101, 1)
    sc.takeoff(TAKEOFF_S, 100)
    sc.kill(FLOWN_S, NO, 100, pos=ON_RUNWAY)
    sc.end(FLOWN_S + 0.1, 100, 101, ON_RUNWAY)
    assert not by_acct(sc.result(), 1).botched_landing


def test_a_crash_right_after_takeoff_is_a_failed_takeoff_not_a_landing() -> None:
    assert not _crash(at=TAKEOFF_S + 60).botched_landing


def test_an_aircraft_lost_high_above_the_airfield_was_not_landing() -> None:
    """A wing coming off at 800 m near the field: the samples have them, up to 7 km above it."""
    assert not _crash(HIGH_ABOVE).botched_landing


def test_a_crash_of_a_badly_shot_up_aircraft_is_no_botched_landing() -> None:
    assert not _crash(enemy_damage=0.3).botched_landing


def test_a_scratch_from_the_enemy_does_not_excuse_the_crash() -> None:
    """40 such sorties in the samples, mostly a hit of 0.1-5% minutes earlier. Any attacker involvement makes the loss
    a shoot-down with the attacker's credit: that stays, the flag is only a label."""
    a = _crash(enemy_damage=0.05)
    assert a.botched_landing
    assert (a.outcome, a.loss_cause) == ("shot_down", "attacker")


def test_an_enemy_kill_is_no_botched_landing_however_little_damage_it_took() -> None:
    sc = _airfield_scenario(enemy=True)
    sc.damage(FLOWN_S - 1, 200, 100, 0.05, pos=ON_RUNWAY)
    sc.kill(FLOWN_S, 200, 100, pos=ON_RUNWAY)
    sc.end(FLOWN_S + 0.1, 100, 101, ON_RUNWAY)
    assert not by_acct(sc.result(), 1).botched_landing


def test_an_aircraft_shot_down_near_the_airfield_is_no_botched_landing() -> None:
    sc = _airfield_scenario(enemy=True)
    sc.damage(FLOWN_S - 1, 200, 100, 0.9, pos=ON_RUNWAY)
    sc.kill(FLOWN_S, 200, 100, pos=Pos(ON_RUNWAY.x, ON_RUNWAY.y + 50.0, ON_RUNWAY.z))
    sc.end(FLOWN_S + 0.1, 100, 101, ON_RUNWAY)
    a = by_acct(sc.result(), 1)
    assert a.outcome == "shot_down"
    assert not a.botched_landing


def test_a_bailout_near_the_airfield_is_no_botched_landing() -> None:
    """The pilot left the aircraft in the air on purpose, and it came down at the field on its own."""
    sc = _airfield_scenario()
    sc.end(FLOWN_S - 5, 0, 101)
    sc.remove_bot(FLOWN_S - 5, 101, Pos(ON_RUNWAY.x + 600.0, ON_RUNWAY.y, ON_RUNWAY.z))
    sc.kill(FLOWN_S, NO, 100, pos=ON_RUNWAY)
    a = by_acct(sc.result(), 1)
    assert a.pilot_fate == "bailed_out"
    assert not a.botched_landing


LOW_APPROACH = Pos(ON_RUNWAY.x, ON_RUNWAY.y + 60.0, ON_RUNWAY.z)


def test_a_pilot_shot_dead_in_the_seat_did_not_botch_it() -> None:
    """The enemy hit the pilot and hardly the aircraft: the wreck came down at the field on its own."""
    sc = _airfield_scenario(enemy=True)
    sc.damage(FLOWN_S - 10, 200, 100, 0.03, pos=LOW_APPROACH)
    sc.kill(FLOWN_S - 10, 200, 101, pos=LOW_APPROACH)
    sc.kill(FLOWN_S, NO, 100, pos=ON_RUNWAY)
    sc.end(FLOWN_S + 0.1, 100, 101, ON_RUNWAY)
    assert not by_acct(sc.result(), 1).botched_landing


def test_a_pilot_shot_dead_just_before_the_crash_did_not_botch_it() -> None:
    """An attacker's kill of the pilot is no crash of the pilot's doing, however close to the crash."""
    sc = _airfield_scenario(enemy=True)
    sc.damage(FLOWN_S - 1, 200, 100, 0.03, pos=LOW_APPROACH)
    sc.kill(FLOWN_S - 1, 200, 101, pos=LOW_APPROACH)
    sc.kill(FLOWN_S, NO, 100, pos=ON_RUNWAY)
    sc.end(FLOWN_S + 0.1, 100, 101, ON_RUNWAY)
    assert not by_acct(sc.result(), 1).botched_landing


def test_a_pilot_killed_by_the_crash_itself_still_botched_it() -> None:
    """The log can have the pilot die on the first impact, seconds before the wreck is destroyed."""
    sc = _airfield_scenario()
    sc.kill(FLOWN_S - 3, NO, 101, pos=ON_RUNWAY)
    sc.kill(FLOWN_S, NO, 100, pos=ON_RUNWAY)
    sc.end(FLOWN_S + 0.1, 100, 101, ON_RUNWAY)
    assert by_acct(sc.result(), 1).botched_landing


def test_a_pilot_who_left_the_server_in_the_air_did_not_botch_it() -> None:
    sc = _airfield_scenario(enemy=True)
    sc.damage(FLOWN_S - 100, 200, 100, 0.03, pos=FAR)
    sc.disconnect(FLOWN_S - 10, 1)
    sc.kill(FLOWN_S, NO, 100, pos=ON_RUNWAY)
    sc.end(FLOWN_S + 0.1, 100, 101, ON_RUNWAY)
    assert not by_acct(sc.result(), 1).botched_landing


def test_leaving_the_server_after_the_crash_does_not_undo_it() -> None:
    sc = _airfield_scenario()
    sc.kill(FLOWN_S, NO, 100, pos=ON_RUNWAY)
    sc.disconnect(FLOWN_S + 2, 1)
    sc.end(FLOWN_S + 3, 100, 101, ON_RUNWAY)
    assert by_acct(sc.result(), 1).botched_landing


@pytest.mark.parametrize("destroyed", [False, True])
def test_an_empty_aircraft_belly_landing_after_an_ejection_is_not_botched(destroyed: bool) -> None:
    """The pilot punched out and is on the parachute; the log only says so later."""
    sc = _airfield_scenario()
    sc.declare(FLOWN_S, 101, "BotPlanePilot_Test", 601, parent=-1, pos=LOW_APPROACH)
    sc.land(FLOWN_S + 20, 100, ON_RUNWAY)
    sc.damage(FLOWN_S + 20, NO, 100, 0.6, pos=ON_RUNWAY)
    if destroyed:
        sc.kill(FLOWN_S + 20, NO, 100, pos=ON_RUNWAY)
    sc.end(FLOWN_S + 50, 0, 101)
    sc.remove_bot(FLOWN_S + 50, 101, Pos(ON_RUNWAY.x + 3_000.0, ON_RUNWAY.y, ON_RUNWAY.z))
    assert not by_acct(sc.result(), 1).botched_landing


def test_a_rough_landing_with_the_pilot_dead_is_not_botched() -> None:
    sc = _airfield_scenario(enemy=True)
    sc.damage(FLOWN_S - 30, 200, 100, 0.03, pos=FAR)
    sc.kill(FLOWN_S - 30, 200, 101, pos=FAR)
    sc.land(FLOWN_S, 100, ON_RUNWAY)
    sc.damage(FLOWN_S + 1, NO, 100, 0.6, pos=ON_RUNWAY)
    sc.end(FLOWN_S + 60, 100, 101, ON_RUNWAY)
    assert not by_acct(sc.result(), 1).botched_landing


def test_a_crash_right_after_a_second_takeoff_is_a_failed_takeoff() -> None:
    sc = _airfield_scenario()
    sc.land(400, 100, ON_RUNWAY)
    sc.takeoff(500, 100)
    sc.kill(510, NO, 100, pos=ON_RUNWAY)
    sc.end(510.1, 100, 101, ON_RUNWAY)
    assert not by_acct(sc.result(), 1).botched_landing


def test_a_rough_landing_after_a_short_hop_is_a_failed_takeoff() -> None:
    sc = _airfield_scenario()
    sc.land(60, 100, ON_RUNWAY)
    sc.damage(61, NO, 100, 0.6, pos=ON_RUNWAY)
    sc.takeoff(300, 100)
    sc.land(1_000, 100, ON_RUNWAY)
    sc.end(1_060, 100, 101, ON_RUNWAY)
    assert not by_acct(sc.result(), 1).botched_landing


def _loss_details(result: SortieResult) -> list[str]:
    return [e.detail for e in result.timeline if e.kind in ("shot_down", "destroyed")]


def test_the_timeline_only_calls_the_crash_a_landing_when_it_was_one() -> None:
    """A rough landing, a repair, then a kill far away: the sortie is flagged, its loss entry is the kill."""
    sc = _airfield_scenario(enemy=True)
    sc.land(400, 100, ON_RUNWAY)
    sc.damage(401, NO, 100, 0.55, pos=ON_RUNWAY)
    sc.takeoff(500, 100)
    sc.damage(899, 200, 100, 0.9, pos=FAR)
    sc.kill(900, 200, 100, pos=FAR)
    sc.end(900.1, 100, 101, FAR)
    a = by_acct(sc.result(), 1)
    assert a.botched_landing
    assert _loss_details(a) == [""]


def test_the_timeline_names_a_crashed_landing() -> None:
    assert _loss_details(_crash()) == ["botched_landing"]


def test_a_taxi_accident_is_no_botched_landing() -> None:
    sc = Scenario()
    sc.airfield(1, 601, FIELD)
    sc.player(0, 100, 101, 1)
    sc.kill(FLOWN_S, NO, 100, pos=ON_RUNWAY)
    sc.end(FLOWN_S + 0.1, 100, 101, ON_RUNWAY)
    a = by_acct(sc.result(), 1)
    assert a.taxi_accident
    assert not a.botched_landing


def test_a_gunner_is_never_flagged() -> None:
    sc = Scenario()
    sc.airfield(1, 501, FIELD)
    sc.player(0, 200, 201, 2, aircraft_type="IL-10", country=501)
    sc.player(1, 500, 501, 3, aircraft_type="Turret_IL10", country=501, parent=200)
    sc.takeoff(TAKEOFF_S, 200)
    sc.kill(FLOWN_S, NO, 200, pos=ON_RUNWAY)
    sc.end(FLOWN_S + 0.1, 500, 501, ON_RUNWAY)
    sc.end(FLOWN_S + 0.1, 200, 201, ON_RUNWAY)
    result = sc.result()
    pilot, gunner = by_acct(result, 2), by_acct(result, 3)
    assert gunner.role == "gunner"
    assert not gunner.botched_landing
    assert pilot.botched_landing


# --- rough landings -------------------------------------------------------------------------------------------------


def test_a_landing_that_wrecks_half_the_aircraft_is_botched_though_it_survives() -> None:
    a = _rough_landing(0.55)
    assert a.botched_landing
    assert (a.outcome, a.is_plane_lost) == ("landed", False)


def test_a_clean_landing_is_not_botched() -> None:
    assert not _rough_landing(0.0).botched_landing


def test_a_scrape_on_landing_is_not_botched() -> None:
    """Samples: 190 landings have under 15% of own damage, none between 15% and 45%."""
    assert not _rough_landing(0.05).botched_landing


def test_a_rough_landing_away_from_the_airfields_is_no_botched_landing() -> None:
    assert not _rough_landing(0.55, PAST_THE_RUNWAY).botched_landing


def test_a_rough_landing_of_a_shot_up_aircraft_is_no_botched_landing() -> None:
    assert not _rough_landing(0.55, enemy_damage=0.3).botched_landing


def test_damage_long_after_the_landing_is_not_the_landing() -> None:
    sc = _airfield_scenario()
    sc.land(FLOWN_S, 100, ON_RUNWAY)
    sc.damage(FLOWN_S + 40, NO, 100, 0.55, pos=ON_RUNWAY)
    sc.end(FLOWN_S + 60, 100, 101, ON_RUNWAY)
    assert not by_acct(sc.result(), 1).botched_landing


def test_a_repair_clears_the_damage_of_the_legs_before() -> None:
    """The aircraft took 30% from the enemy, landed clean, was repaired and wrecked its next landing."""
    sc = _airfield_scenario(enemy=True)
    sc.damage(300, 200, 100, 0.3, pos=FAR)
    sc.land(400, 100, ON_RUNWAY)
    sc.takeoff(500, 100)
    sc.land(FLOWN_S + 300, 100, ON_RUNWAY)
    sc.damage(FLOWN_S + 301, NO, 100, 0.55, pos=ON_RUNWAY)
    sc.end(FLOWN_S + 360, 100, 101, ON_RUNWAY)
    assert by_acct(sc.result(), 1).botched_landing


# --- config ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rules", "expected"),
    [
        (ReplayRules(botched_landing_radius_m=1_000.0), False),
        (ReplayRules(botched_landing_radius_m=3_000.0), True),
    ],
)
def test_the_radius_is_a_rule(rules: ReplayRules, expected: bool) -> None:
    sc = _airfield_scenario()
    sc.kill(FLOWN_S, NO, 100, pos=ON_RUNWAY)
    sc.end(FLOWN_S + 0.1, 100, 101, ON_RUNWAY)
    assert by_acct(sc.result(rules), 1).botched_landing is expected
