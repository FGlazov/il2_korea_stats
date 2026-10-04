"""Replay scenarios for player gunners: kill sharing with the pilot and gunner deaths
(design_doc/13_game_rules.md, Kills and credit; FR-ING-21, FR-ING-22).

Ids: IL-10 pilot acct 2 (aircraft 200, bot 201, country 501), gunner acct 3 (turret 500, gunner bot 501, country 501),
AI victim 301 (F-86A-5, country 601, an enemy of the gunner), AI friendly 302 (country 502, the gunner's coalition).
"""

from il2ks.core.replay.result import Counterpart, MissionResult, SortieResult
from tests.unit.replay.builder import FAR, NO, Scenario, by_acct


def _crew() -> Scenario:
    """An IL-10 with a player pilot and a player gunner, both in the air, plus the AI aircraft they may shoot."""
    sc = Scenario()
    sc.fly(200, 201, 2, aircraft_type="IL-10", country=501)
    sc.player(1, 500, 501, 3, aircraft_type="Turret_IL10", country=501, parent=200)
    sc.declare(0, 301, "F-86A-5", 601)
    sc.declare(0, 302, "F-86A-5", 502)
    return sc


def _pairs(result: MissionResult) -> list[tuple[int | None, int | None]]:
    return [(k.victim_sortie_index, k.killer_sortie_index) for k in result.kills]


def _death_record(s: SortieResult) -> tuple[bool, str, str]:
    return s.is_death, s.pilot_status, s.loss_cause


# --- a gunner's kill is an assist for the pilot ----------------------------------------------------------------


def test_gunner_kill_gives_the_pilot_an_assist() -> None:
    sc = _crew()
    sc.damage(100, 500, 301, 1.0)
    sc.kill(101, 500, 301)
    sc.end(200, 500, 501)
    sc.end(200, 200, 201)
    result = sc.result()
    pilot, gunner = by_acct(result, 2), by_acct(result, 3)
    by_killer = {k.killer_sortie_index: k for k in result.kills}
    assert set(by_killer) == {gunner.index, pilot.index}
    assert (by_killer[gunner.index].credit, by_killer[gunner.index].killer_type) == ("kill", "Turret_IL10")
    kill, assist = by_killer[gunner.index], by_killer[pilot.index]
    assert (assist.credit, assist.via, assist.is_friendly, assist.killer_type) == ("assist", "direct", False, "IL-10")
    assert (assist.tick, assist.victim_object_id) == (kill.tick, kill.victim_object_id)
    assert (gunner.kills_air, gunner.assists) == (1, 0)
    assert (pilot.kills_air, pilot.assists, pilot.assists_air, pilot.assists_ground) == (0, 1, 1, 0)
    assert "assist" in [e.kind for e in pilot.timeline]


def test_pilot_already_credited_gets_no_second_result() -> None:
    """The pilot damaged the victim too (an assist on its own), so the gunner's kill adds nothing: one row per pair."""
    sc = _crew()
    sc.damage(99, 200, 301, 0.4)
    sc.damage(100, 500, 301, 0.2)
    sc.kill(101, 500, 301)
    sc.end(200, 500, 501)
    sc.end(200, 200, 201)
    result = sc.result()
    pilot, gunner = by_acct(result, 2), by_acct(result, 3)
    assert sorted((k.killer_sortie_index, k.credit) for k in result.kills if k.killer_sortie_index is not None) == [
        (pilot.index, "assist"),
        (gunner.index, "kill"),
    ]
    assert pilot.assists == 1


def test_pilot_kill_with_a_gunner_assist_adds_nothing() -> None:
    sc = _crew()
    sc.damage(99, 500, 301, 0.3)
    sc.damage(100, 200, 301, 0.7)
    sc.kill(101, 200, 301)
    sc.end(200, 500, 501)
    sc.end(200, 200, 201)
    result = sc.result()
    pilot, gunner = by_acct(result, 2), by_acct(result, 3)
    assert sorted((k.killer_sortie_index, k.credit) for k in result.kills) == [
        (pilot.index, "kill"),
        (gunner.index, "assist"),
    ]
    assert (pilot.kills_air, pilot.assists, gunner.assists) == (1, 0, 1)


def test_two_gunners_credited_for_one_victim_give_the_pilot_one_assist() -> None:
    """Two player gunners credited for the same victim (kill and assist): the pilot gets a single assist row."""
    sc = _crew()
    sc.player(2, 600, 601, 4, aircraft_type="Turret_IL10", country=501, parent=200)
    sc.damage(99, 600, 301, 0.3)
    sc.damage(100, 500, 301, 0.7)
    sc.kill(101, 500, 301)
    for turret, bot in ((500, 501), (600, 601), (200, 201)):
        sc.end(200, turret, bot)
    result = sc.result()
    pairs = [(k.victim_object_id, k.killer_sortie_index) for k in result.kills]
    assert len(pairs) == len(set(pairs))
    assert by_acct(result, 2).assists == 1


def test_friendly_fire_by_a_gunner_gives_the_pilot_nothing() -> None:
    sc = _crew()
    sc.damage(100, 500, 302, 1.0)
    sc.kill(101, 500, 302)
    sc.end(200, 500, 501)
    sc.end(200, 200, 201)
    result = sc.result()
    pilot, gunner = by_acct(result, 2), by_acct(result, 3)
    (kill,) = result.kills
    assert (kill.killer_sortie_index, kill.is_friendly) == (gunner.index, True)
    assert (pilot.kills_air, pilot.assists) == (0, 0)


def test_gunner_of_an_ai_pilot_has_no_pilot_to_credit() -> None:
    sc = Scenario()
    sc.declare(0, 200, "IL-10", 501)  # AI pilot: the aircraft isn't a player sortie
    sc.player(1, 500, 501, 3, aircraft_type="Turret_IL10", country=501, parent=200)
    sc.declare(0, 301, "F-86A-5", 601)
    sc.damage(100, 500, 301, 1.0)
    sc.kill(101, 500, 301)
    sc.end(200, 500, 501)
    result = sc.result()
    gunner = by_acct(result, 3)
    assert gunner.parent_sortie_index is None
    assert [k.killer_sortie_index for k in result.kills] == [gunner.index]


# --- a gunner's death is recorded on the gunner sortie only ----------------------------------------------------


def test_gunner_dies_with_the_shot_down_aircraft_without_an_extra_kill() -> None:
    sc = _crew()
    sc.fly_a()
    sc.damage(100, 100, 200, 0.6)
    sc.kill(101, 100, 200)
    sc.end(101.1, 500, 501)
    sc.end(101.1, 200, 201)
    result = sc.result()
    a, pilot, gunner = by_acct(result, 1), by_acct(result, 2), by_acct(result, 3)
    assert _death_record(gunner) == (True, "dead", "attacker")
    assert (gunner.outcome, gunner.aircraft_status) == ("shot_down", "destroyed")
    assert _death_record(pilot) == (True, "dead", "attacker")
    # The killer gets one air kill for the aircraft, not one more for the gunner
    assert (a.kills_air, a.assists) == (1, 0)
    assert _pairs(result) == [(pilot.index, a.index)]
    # The gunner's own timeline names the killer, though no KillResult has the gunner as victim
    (shot_down,) = [e for e in gunner.timeline if e.kind == "shot_down"]
    assert shot_down.counterpart == Counterpart("F-86A-5", a.index, 2)


def test_gunner_killed_alone_records_the_death_and_the_attacker() -> None:
    """The gunner bot gets an AType 3 from an enemy while the aircraft survives: the gunner sortie records the death
    with `loss_cause = attacker` (it used to say `self`), and nobody gets a kill, nor does the pilot die."""
    sc = _crew()
    sc.fly_a()
    sc.damage(100, 100, 501, 1.0)
    sc.kill(100.5, 100, 501)
    sc.end(150, 500, 501)
    sc.end(150, 200, 201)
    result = sc.result()
    a, pilot, gunner = by_acct(result, 1), by_acct(result, 2), by_acct(result, 3)
    assert _death_record(gunner) == (True, "dead", "attacker")
    assert gunner.pilot_fate == "in_aircraft"
    assert (pilot.is_death, pilot.pilot_status, pilot.is_plane_lost) == (False, "healthy", False)
    assert result.kills == ()
    assert (a.kills_air, a.kills_ground, a.assists) == (0, 0, 0)
    (killed,) = [e for e in gunner.timeline if e.kind == "killed"]
    assert killed.counterpart == Counterpart("F-86A-5", a.index, 2)
    assert [e.kind for e in pilot.timeline if e.kind in ("killed", "died", "shot_down")] == []


def test_turret_destroyed_counts_as_the_gunner_dying() -> None:
    sc = _crew()
    sc.fly_a()
    sc.damage(100, 100, 500, 1.0)
    sc.kill(100.5, 100, 500)
    sc.end(150, 500, 501)
    sc.end(150, 200, 201)
    result = sc.result()
    assert _death_record(by_acct(result, 3)) == (True, "dead", "attacker")
    assert result.kills == ()


def test_gunner_who_bailed_out_is_not_killed_by_the_turret_going_down() -> None:
    sc = _crew()
    sc.gunner_bailout(100, 501, 200, FAR)
    sc.end(100.2, 0, 501)
    sc.kill(110, NO, 500)  # the turret goes down with the abandoned aircraft
    gunner = by_acct(sc.result(), 3)
    assert gunner.pilot_fate == "bailed_out"
    assert not gunner.is_death


def test_gunner_killed_by_the_environment_gets_a_died_entry() -> None:
    sc = _crew()
    sc.kill(100.5, NO, 501)
    sc.end(150, 500, 501)
    sc.end(150, 200, 201)
    gunner = by_acct(sc.result(), 3)
    assert _death_record(gunner) == (True, "dead", "self")
    (died,) = [e for e in gunner.timeline if e.kind == "died"]
    assert died.counterpart is None


# --- a gunner sortie always closes (doc 12: AType 4, or AType 16 for the bot when there is no AType 4) --------------


def test_gunner_without_a_sortie_end_closes_on_the_bot_removal() -> None:
    """19 of 104 sample gunner sorties have no AType 4: the gunner bot's AType 16 ends the sortie like a pilot's."""
    sc = _crew()
    sc.remove_bot(50, 501, FAR)
    sc.land(100, 200)
    sc.end(110, 200, 201)
    gunner = by_acct(sc.result(), 3)
    assert (gunner.end_tick, gunner.pilot_fate, gunner.disconnected) == (50 * 50, "disconnected", True)
    assert gunner.outcome == "unknown"  # left an aircraft that was still flying; never `in_flight` once closed
    assert gunner.ammo_left is None


def test_gunner_leaving_a_flying_aircraft_is_unknown_not_in_flight() -> None:
    """AType 4 for the gunner while the pilot flies on (18 sample gunners): the sortie is closed, so not `in_flight`."""
    sc = _crew()
    sc.end(50, 500, 501)
    sc.land(100, 200)
    sc.end(110, 200, 201)
    pilot, gunner = by_acct(sc.result(), 2), by_acct(sc.result(), 3)
    assert (gunner.outcome, gunner.pilot_fate, gunner.is_death, gunner.is_plane_lost) == (
        "unknown",
        "in_aircraft",
        False,
        False,
    )
    assert pilot.outcome == "landed"


def test_gunner_sortie_open_at_the_mission_end_is_closed_by_it() -> None:
    sc = _crew()
    sc.mission_end(100)
    gunner = by_acct(sc.result(), 3)
    assert (gunner.outcome, gunner.pilot_fate) == ("airborne", "in_aircraft")
    assert gunner.ended_by_mission_end
    assert gunner.end_tick >= 100 * 50


def test_parent_destroyed_minutes_after_the_gunner_left_is_not_the_gunners_death() -> None:
    """The 300 s post-end window is for pilots: the aircraft flew on after the gunner left it (2 sample sorties)."""
    sc = _crew()
    sc.fly(600, 601, 4)  # an enemy F-86A-5 to do the shooting
    sc.end(50, 500, 501)
    sc.damage(249.5, 600, 200, 0.5)
    sc.kill(250, 600, 200)
    sc.end(250.1, 200, 201)
    result = sc.result()
    gunner = by_acct(result, 3)
    assert (gunner.is_death, gunner.is_plane_lost, gunner.outcome) == (False, False, "unknown")
    assert by_acct(result, 2).outcome == "shot_down"


def test_turret_id_reused_for_another_aircraft_after_the_gunner_left_keeps_the_old_parent() -> None:
    """Real shape: the gunner leaves, and the same turret ID is later declared again under a different aircraft for
    the next gunner. The ended sortie must stay attached to its own aircraft (it used to be re-linked to the new)."""
    sc = _crew()
    sc.end(50, 500, 501)
    sc.land(100, 200)
    sc.end(110, 200, 201)
    sc.fly(600, 601, 4, aircraft_type="IL-10", country=501, spawn=300, up=305)
    sc.player(310, 500, 501, 5, aircraft_type="Turret_IL10", country=501, parent=600)
    sc.land(400, 600)
    sc.end(410, 600, 601)
    sc.end(410, 500, 501)
    result = sc.result()
    first, pilot = by_acct(result, 3), by_acct(result, 2)
    assert first.parent_sortie_index == pilot.index
    assert (first.outcome, first.takeoff_tick is not None) == ("unknown", True)
