"""Replay scenario tests: outcomes, kills and credit. One rule per test (the executable rulebook, TD-21)."""

import pytest

from il2ks.core.logparse.events import WheelsOnEvent
from tests.unit.replay.builder import FAR, GROUND, NO, Scenario, by_acct, ids


def test_landed() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.land(600, 100)
    sc.end(650, 100, 101)
    sc.remove_bot(650, 101, GROUND)
    a = by_acct(sc.result(), 1)
    assert a.outcome == "landed"
    assert (a.pilot_fate, a.pilot_fate_source) == ("in_aircraft", "event")
    assert (a.takeoffs, a.landings) == (1, 1)
    assert a.flight_time_s == pytest.approx(595.0)
    assert (a.is_death, a.is_plane_lost, a.suspected_early_bailout) == (False, False, False)
    assert (a.pilot_status, a.aircraft_status, a.loss_cause) == ("healthy", "unharmed", "none")
    assert a.ammo_loaded.bullets == 100
    assert a.ammo_left is not None
    assert a.ammo_left.bullets == 40


def test_not_taken_off() -> None:
    sc = Scenario()
    sc.player(0, 100, 101, 1)
    sc.end(30, 100, 101)
    a = by_acct(sc.result(), 1)
    assert a.outcome == "not_taken_off"
    assert a.takeoff_tick is None
    assert not a.is_death


def test_air_start_counts_as_takeoff() -> None:
    sc = Scenario()
    sc.player(0, 100, 101, 1, in_air=0, pos=FAR)
    sc.end(100, 100, 101)
    a = by_acct(sc.result(), 1)
    assert a.spawn_type == "air"
    assert a.takeoff_tick == 0
    assert a.flight_time_s == pytest.approx(100.0)


def test_shot_down_by_player_gives_kill_credit() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(100, 200, 100, 0.6)
    sc.kill(101, 200, 100)
    sc.end(101.1, 100, 101)
    sc.remove_bot(101.1, 101, FAR)
    result = sc.result()
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.outcome, a.loss_cause, a.aircraft_status) == ("shot_down", "attacker", "destroyed")
    assert a.is_death
    assert a.is_plane_lost
    assert b.kills_air == 1
    (kill,) = result.kills
    assert (kill.killer_sortie_index, kill.victim_sortie_index) == (b.index, a.index)
    assert (kill.credit, kill.via, kill.is_friendly, kill.killer_type) == ("kill", "direct", False, "MiG-15bis")
    assert [e.kind for e in a.timeline if e.kind == "shot_down"] == ["shot_down"]
    assert [e.kind for e in b.timeline if e.kind == "kill"] == ["kill"]


def test_assist_split_most_damage_gets_the_kill() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.fly(500, 501, 3, aircraft_type="MiG-15bis", country=501)
    sc.damage(100, 500, 100, 0.3)
    sc.damage(101, 200, 100, 0.7)
    sc.kill(102, 200, 100)
    sc.end(102.1, 100, 101)
    result = sc.result()
    b, c = by_acct(result, 2), by_acct(result, 3)
    assert (b.kills_air, b.assists) == (1, 0)
    assert (c.kills_air, c.assists) == (0, 1)
    assert [(k.killer_sortie_index, k.credit) for k in result.kills] == [(b.index, "kill"), (c.index, "assist")]


def test_most_damage_wins_when_the_kill_line_has_no_attacker() -> None:
    """AID:-1 on the kill line: credit goes by damage (FR-ING-22 style)."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.fly(500, 501, 3, aircraft_type="MiG-15bis", country=501)
    sc.damage(100, 500, 100, 0.2)
    sc.damage(101, 200, 100, 0.5)
    sc.kill(102, NO, 100)
    sc.end(102.1, 100, 101)
    result = sc.result()
    assert [(k.killer_sortie_index, k.credit) for k in result.kills] == [
        (by_acct(result, 2).index, "kill"),
        (by_acct(result, 3).index, "assist"),
    ]


def test_tiny_damage_is_no_assist() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.fly(500, 501, 3, aircraft_type="MiG-15bis", country=501)
    sc.damage(100, 500, 100, 0.001)
    sc.damage(101, 200, 100, 0.9)
    sc.kill(102, 200, 100)
    sc.end(102.1, 100, 101)
    assert by_acct(sc.result(), 3).assists == 0


def test_ai_kills_player_without_pvp_row_partner() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.declare(0, 300, "MiG-15bis", 501)
    sc.damage(100, 300, 100, 1.0)
    sc.kill(101, 300, 100)
    sc.end(101.1, 100, 101)
    result = sc.result()
    a = by_acct(result, 1)
    assert a.loss_cause == "attacker"
    (kill,) = result.kills
    assert kill.killer_sortie_index is None
    assert kill.killer_type == "MiG-15bis"
    assert kill.victim_sortie_index == a.index


def test_player_kills_ai_air_and_ground_counters() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.declare(0, 300, "MiG-15bis", 501)
    sc.declare(0, 401, "M46 Patton", 501)
    sc.damage(100, 100, 300, 1.0)
    sc.kill(101, 100, 300)
    sc.damage(110, 100, 401, 1.0)
    sc.kill(111, 100, 401)
    sc.end(200, 100, 101)
    result = sc.result()
    a = by_acct(result, 1)
    assert (a.kills_air, a.kills_ground, a.assists) == (1, 1, 0)
    assert {(k.victim_kind, k.victim_sortie_index) for k in result.kills} == {("air", None), ("ground", None)}


def test_ai_against_ai_has_no_kill_row() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.declare(0, 300, "MiG-15bis", 501)
    sc.declare(0, 301, "F-86A-5", 601)
    sc.damage(100, 300, 301, 1.0)
    sc.kill(101, 300, 301)
    sc.end(200, 100, 101)
    assert sc.result().kills == ()


def test_friendly_fire_is_flagged_and_not_a_kill() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.declare(0, 301, "F-86A-5", 601)  # AI, same coalition as A
    sc.damage(100, 100, 301, 1.0)
    sc.kill(101, 100, 301)
    sc.end(200, 100, 101)
    result = sc.result()
    (kill,) = result.kills
    assert kill.is_friendly
    assert by_acct(result, 1).kills_air == 0


def test_explosion_hits_never_count() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.hit(100, 100, 200, ammo="explosion")
    sc.hit(100, 100, 200, ammo="BULLET_12-7_USA_API")
    sc.end(200, 100, 101)
    result = sc.result()
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert [(h.ammo, h.hits_given, h.hits_received) for h in a.ammo_hits] == [("BULLET_12-7_USA_API", 1, 0)]
    assert [(h.ammo, h.hits_given, h.hits_received) for h in b.ammo_hits] == [("BULLET_12-7_USA_API", 0, 1)]
    assert a.damage[0].hits_dealt == 1


def test_redeclaration_does_not_break_the_sortie() -> None:
    """Doc 12: AType 12 re-emits the aircraft and pilot (PID:-1 for the pilot) before the sortie end."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(50, 200, 100, 0.2)
    sc.land(190, 100)
    sc.declare(195, 100, "F-86A-5", 601, pos=GROUND)
    sc.declare(195, 101, "BotPlanePilot_Test", 601, parent=-1, pos=GROUND)
    sc.damage(195.5, 200, 100, 0.2)  # still linked to the sortie after the re-declaration
    sc.end(196, 100, 101)
    sc.remove_bot(196, 101, GROUND)
    result = sc.result()
    a = by_acct(result, 1)
    assert len(result.sorties) == 2
    assert a.damage_taken == pytest.approx(0.4)
    assert a.aircraft_status == "damaged"
    (exchange,) = a.damage
    assert exchange.counterpart.sortie_index == by_acct(result, 2).index
    assert exchange.damage_taken == pytest.approx(0.4)
    assert a.outcome == "landed"


def test_sortie_forced_by_mission_end_in_the_air_is_airborne() -> None:
    """Doc 13: a forced sortie's outcome is the aircraft's state when the mission ended; the fate is in_aircraft."""
    sc = Scenario()
    sc.fly_a()
    sc.mission_end(1000)
    sc.kill(1000.1, NO, 100)  # despawn cleanup is logged as destruction
    sc.end(1000.1, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.outcome, a.pilot_fate, a.pilot_fate_source) == ("airborne", "in_aircraft", "event")
    assert a.ended_by_mission_end
    assert (a.is_death, a.is_plane_lost) == (False, False)
    (end_entry,) = [e for e in a.timeline if e.kind == "sortie_end"]
    assert end_entry.detail == "mission_end"


def test_open_sortie_at_finish_after_mission_end_is_airborne_and_inferred() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.mission_end(1000)
    a = by_acct(sc.result(), 1)
    assert (a.outcome, a.pilot_fate, a.pilot_fate_source) == ("airborne", "in_aircraft", "inferred")
    assert a.ended_by_mission_end


def test_sortie_forced_on_the_ground_after_landing_is_landed() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.land(600, 100)
    sc.mission_end(1000)
    sc.end(1000.1, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.outcome, a.pilot_fate) == ("landed", "in_aircraft")
    assert a.ended_by_mission_end


def test_sortie_forced_after_a_landing_away_from_the_airfields_is_ditched() -> None:
    sc = Scenario()
    sc.airfield(1, 601, GROUND)
    sc.fly_a()
    sc.land(600, 100, FAR)
    sc.mission_end(1000)
    sc.end(1000.1, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.outcome, a.pilot_fate, a.ended_by_mission_end) == ("ditched", "in_aircraft", True)


def test_sortie_forced_before_taking_off_is_not_taken_off() -> None:
    sc = Scenario()
    sc.player(0, 200, 201, 2, aircraft_type="MiG-15bis", country=501)
    sc.mission_end(1000)
    sc.end(1000.2, 200, 201)
    b = by_acct(sc.result(), 2)
    assert (b.outcome, b.pilot_fate, b.ended_by_mission_end) == ("not_taken_off", "in_aircraft", True)


def test_loss_before_the_mission_end_keeps_its_outcome_and_is_not_forced() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(300, 200, 100, 1.0)
    sc.kill(300, 200, 100)
    sc.mission_end(1000)
    sc.end(1000.1, 100, 101)
    sc.end(1000.2, 200, 201)
    a = by_acct(sc.result(), 1)
    assert (a.outcome, a.is_plane_lost, a.ended_by_mission_end) == ("shot_down", True, False)


def test_ordinary_sortie_is_not_ended_by_the_mission_end() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.land(600, 100)
    sc.end(650, 100, 101)
    a = by_acct(sc.result(), 1)
    assert not a.ended_by_mission_end
    (end_entry,) = [e for e in a.timeline if e.kind == "sortie_end"]
    assert end_entry.detail == "landed"


def test_sortie_end_long_after_mission_end_is_not_forced() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.mission_end(1000)
    sc.land(1005, 100)
    sc.end(1020, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.outcome, a.ended_by_mission_end) == ("landed", False)


def test_open_sortie_at_finish_without_mission_end_is_in_flight() -> None:
    sc = Scenario()
    sc.fly_a()
    a = by_acct(sc.result(), 1)
    assert a.outcome == "in_flight"
    assert a.pilot_fate == "unknown"


def test_gunner_sortie_credits_the_gunner_and_links_the_pilot() -> None:
    sc = Scenario()
    sc.fly(200, 201, 2, aircraft_type="IL-10", country=501)
    sc.player(1, 500, 501, 3, aircraft_type="Turret_IL10", country=501, parent=200)
    sc.declare(0, 301, "F-86A-5", 601)
    sc.damage(100, 500, 301, 1.0)
    sc.kill(101, 500, 301)
    sc.end(200, 500, 501)
    sc.end(200, 200, 201)
    result = sc.result()
    pilot, gunner = by_acct(result, 2), by_acct(result, 3)
    assert gunner.role == "gunner"
    assert gunner.parent_sortie_index == pilot.index
    assert gunner.kills_air == 1
    assert pilot.role == "pilot"
    assert gunner.takeoff_tick is not None


def test_gunner_bailout_event_sets_fate_from_the_event() -> None:
    sc = Scenario()
    sc.fly(200, 201, 2, aircraft_type="IL-10", country=501)
    sc.player(1, 500, 501, 3, aircraft_type="Turret_IL10", country=501, parent=200)
    sc.gunner_bailout(100, 501, 200, FAR)
    sc.end(100.5, 0, 501)
    gunner = by_acct(sc.result(), 3)
    assert (gunner.pilot_fate, gunner.pilot_fate_source) == ("bailed_out", "event")


def test_object_types_seen_and_unknown() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.declare(1, 900, "Mystery Truck [123,4]", 501)
    sc.end(100, 100, 101)
    result = sc.result()
    assert "F-86A-5" in result.object_types_seen
    assert "Mystery Truck" in result.unknown_object_types
    assert not any(t.startswith("Bot") for t in result.object_types_seen)


def test_parachute_instance_numbers_are_one_object_type() -> None:
    """Regression: `CParachute_<n>` registered one GameObject per parachute (e2e run, 2026-10-03). doc 14, Catalog."""
    sc = Scenario()
    sc.fly_a()
    sc.declare(1, 900, "CParachute_2361344", 501)
    sc.declare(2, 901, "CParachute_2361999", 501)
    sc.end(100, 100, 101)
    seen = sc.result().object_types_seen
    assert "CParachute" in seen
    assert not any(t.startswith("CParachute_") for t in seen)


def test_wheels_on_after_kill_is_ground_contact_only() -> None:
    """A wreck's AType 31 doesn't flip the destroyed aircraft to 'on the ground' (FR-ING-17 uses it as contact)."""
    sc = Scenario()
    sc.fly_a()
    sc.kill(100, NO, 100)
    sc.add(WheelsOnEvent(tick=5000, object_id=ids(100), pos=GROUND))
    sc.end(100.1, 100, 101)
    a = by_acct(sc.result(), 1)
    assert a.outcome == "crashed"
    assert a.loss_cause == "self"


def test_destroyed_ai_object_id_reused_is_a_new_object() -> None:
    """Doc 12 samples: the game recycles IDs (15k re-declarations with another type in 7 missions, and same-type
    ones after a destruction). A kill of the new object must not be swallowed by the old, destroyed one."""
    sc = Scenario()
    sc.fly_a()
    sc.declare(0, 401, "M46 Patton", 501)
    sc.damage(10, 100, 401, 1.0)
    sc.kill(11, 100, 401)
    sc.declare(50, 401, "M46 Patton", 501)  # same ID, same type, after the first one died
    sc.damage(60, 100, 401, 1.0)
    sc.kill(61, 100, 401)
    sc.declare(90, 402, "M46 Patton", 501)
    sc.declare(95, 402, "BotPlanePilot_Test", 501)  # another type on a known ID
    sc.end(200, 100, 101)
    result = sc.result()
    assert by_acct(result, 1).kills_ground == 2
    assert len(result.kills) == 2


def test_wreck_landing_after_destruction_is_not_a_landing() -> None:
    """Real logs write AType 6 for the falling wreck; flight time stops at the destruction."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(100, 200, 100, 1.0)
    sc.kill(100, 200, 100)
    sc.land(102, 100)
    sc.end(110, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.takeoffs, a.landings, a.landing_tick) == (1, 0, None)
    assert a.flight_time_s == pytest.approx(95.0)
    assert "landing" not in [e.kind for e in a.timeline]


def test_crew_and_equipment_are_never_kill_victims() -> None:
    """Parachutes, ejection seats and vehicle turrets (catalog classes `equipment`, `crew`) aren't kills; a real AI
    aircraft shot in the same scenario still is (design_doc/13_game_rules.md, Kills and credit)."""
    sc = Scenario()
    sc.fly_a()
    sc.declare(0, 301, "MiG-15bis", 501)
    sc.declare(0, 310, "CParachute", 501)
    sc.declare(0, 311, "ESeat_MiG-15bis", 501)
    sc.declare(0, 312, "VehicleTurret", 501)
    for t, target in ((100, 310), (101, 311), (102, 312), (103, 301)):
        sc.damage(t, 100, target, 1.0)
        sc.kill(t + 0.5, 100, target)
    sc.end(200, 100, 101)
    result = sc.result()
    assert [k.victim_object_id for k in result.kills] == [ids(301)]
    assert by_acct(result, 1).kills_air == 1
