"""Replay scenarios for player gunners: kill sharing with the pilot (E2) and gunner deaths (E3).

Ids: IL-10 pilot acct 2 (aircraft 200, bot 201, country 501), gunner acct 3 (turret 500, gunner bot 501, country 501),
AI victim 301 (F-86A-5, country 601, an enemy of the gunner), AI friendly 302 (country 502, the gunner's coalition).
"""

from il2ks.core.replay.result import MissionResult, SortieResult
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


# --- E2: a gunner's kill is an assist for the pilot ----------------------------------------------------------------


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
    assert (pilot.kills_air, pilot.assists) == (0, 1)
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


# --- E3: a gunner's death is recorded on the gunner sortie only ----------------------------------------------------


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
