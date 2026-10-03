"""Combat role and time on target (FR-WEB-19, FR-WEB-20, doc 13; OQ-27, OQ-29).

Ids: player A (acct 1) flies F-86A-5 (aircraft 100, bot 101, coalition 2), spawning at 0 s and taking off at 5 s.
Targets: 400 is an enemy (country 501, coalition 1) "Block_Test" static at `TARGET`; 410 a friendly (601) one.
"""

import dataclasses

import pytest

from il2ks.core.catalog.loader import Catalog, ObjectClass, ObjectInfo
from il2ks.core.logparse.events import (
    ObjectId,
    PlayerSpawnEvent,
    Pos,
    RocketFiredEvent,
    StoreReleaseEvent,
)
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import SortieResult
from il2ks.core.replay.state import run
from tests.unit.replay.builder import OBJECTS, Scenario, by_acct, tick

EXTRA: dict[str, ObjectClass] = {
    "Block_Test": "static",
    "Truck_Test": "vehicle",
    "Flak_Test": "aaa",
    "Boat_Test": "ship",
}
TARGET = Pos(20_000.0, 30.0, 20_000.0)
ABOVE = Pos(20_000.0, 2_500.0, 20_000.0)  # over the target, 2.5 km up
FAR_FROM_TARGET = Pos(25_000.0, 2_500.0, 20_000.0)  # 5 km away


class _Catalog(Catalog):
    def lookup(self, object_type: str) -> ObjectInfo:
        known: dict[str, ObjectClass] = {**OBJECTS, **EXTRA}
        cls = known.get(object_type)
        if cls is None:
            return ObjectInfo(object_type, object_type, "unknown", is_playable=False, is_known=False)
        return ObjectInfo(object_type, object_type, cls, is_playable=False, is_known=True)


def _loadout(sc: Scenario, *, bombs: int, rockets: int, aircraft: int = 100) -> None:
    """The builder's spawn always carries 4 rockets: replace the ammo of one aircraft's AType 10."""
    for i, event in enumerate(sc.events):
        if isinstance(event, PlayerSpawnEvent) and event.aircraft_id == aircraft:
            sc.events[i] = dataclasses.replace(event, bombs=bombs, rockets=rockets)


def _strike(*, bombs: int = 2, rockets: int = 0, aircraft_type: str = "F-86A-5", up: float = 5) -> Scenario:
    sc = Scenario()
    sc.fly(100, 101, 1, aircraft_type=aircraft_type, up=up)
    _loadout(sc, bombs=bombs, rockets=rockets)
    sc.declare(0, 400, "Block_Test", 501, pos=TARGET)
    sc.declare(0, 410, "Block_Test", 601, pos=Pos(30_000.0, 30.0, 30_000.0))
    return sc


def _release(sc: Scenario, t: float, pos: Pos = ABOVE, *, rocket: bool = False) -> None:
    if rocket:
        sc.add(RocketFiredEvent(tick=tick(t), object_id=ObjectId(100), pos=pos, rocket_id=ObjectId(900)))
    else:
        sc.add(StoreReleaseEvent(tick=tick(t), object_id=ObjectId(100), pos=pos, store_id=ObjectId(900)))


def _a(sc: Scenario, rules: ReplayRules | None = None, *, end: float = 600) -> SortieResult:
    sc.end(end, 100, 101)
    return by_acct(run(sc.events, _Catalog(), rules), 1)


# --- combat role ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bombs", "rockets", "role"),
    [
        (0, 0, "air_superiority"),  # guns only; drop tanks have no ammo count
        (2, 0, "attack"),  # bombs; napalm tanks are counted as bombs too
        (0, 6, "attack"),  # rockets
        (2, 6, "attack"),
    ],
)
def test_role_comes_from_the_loaded_bombs_and_rockets(bombs: int, rockets: int, role: str) -> None:
    assert _a(_strike(bombs=bombs, rockets=rockets)).combat_role == role


def test_an_attacker_class_aircraft_is_always_attack() -> None:
    sc = Scenario()
    sc.fly(100, 101, 1, aircraft_type="IL-10")
    _loadout(sc, bombs=0, rockets=0)
    a = _a(sc)
    assert (a.combat_role, a.time_on_target_s) == ("attack", 0.0)


def test_a_gunner_has_no_role_and_no_time_on_target() -> None:
    sc = Scenario()
    sc.fly(200, 201, 2, aircraft_type="IL-10", country=501)
    sc.player(1, 500, 501, 3, aircraft_type="Turret_IL10", country=501, parent=200)
    sc.end(100, 500, 501)
    sc.end(100, 200, 201)
    result = run(sc.events, _Catalog())
    pilot, gunner = by_acct(result, 2), by_acct(result, 3)
    assert (pilot.combat_role, gunner.combat_role, gunner.time_on_target_s) == ("attack", None, None)


def test_air_superiority_sortie_has_no_time_on_target_even_with_releases() -> None:
    sc = _strike(bombs=0, rockets=0)  # a drop tank released over an enemy base
    _release(sc, 100)
    a = _a(sc)
    assert (a.combat_role, a.time_on_target_s) == ("air_superiority", None)


# --- time on target ------------------------------------------------------------------------------------------------


def test_attack_without_any_release_is_zero() -> None:
    assert _a(_strike()).time_on_target_s == 0.0


def test_release_near_an_enemy_object_counts_the_lead_in() -> None:
    sc = _strike()
    _release(sc, 100)
    assert _a(sc).time_on_target_s == 60.0


def test_rocket_salvo_counts_like_a_store() -> None:
    sc = _strike(bombs=0, rockets=6)
    _release(sc, 100, rocket=True)
    assert _a(sc).time_on_target_s == 60.0


def test_release_far_from_every_enemy_object_does_not_count() -> None:
    sc = _strike()
    _release(sc, 100, FAR_FROM_TARGET)  # jettisoned on the way
    assert _a(sc).time_on_target_s == 0.0


def test_altitude_is_ignored() -> None:
    """2.5 km up and 1.1 km to the side: 2.7 km away in 3D, but the rule is horizontal only (so is 4 km up)."""
    sc = _strike()
    _release(sc, 100, Pos(21_000.0, 4_000.0, 20_500.0))
    assert _a(sc).time_on_target_s == 60.0


def test_radius_is_horizontal_and_a_config_value() -> None:
    near, outside = Pos(22_900.0, 800.0, 20_000.0), Pos(23_100.0, 800.0, 20_000.0)
    for pos, expected in ((near, 60.0), (outside, 0.0)):
        sc = _strike()
        _release(sc, 100, pos)
        assert _a(sc).time_on_target_s == expected
    sc = _strike()
    _release(sc, 100, outside)
    assert _a(sc, ReplayRules(tot_target_radius_m=4_000.0)).time_on_target_s == 60.0


def test_target_in_the_next_grid_cell_is_found() -> None:
    """Cells are as wide as the radius: a release and a target 100 m apart can sit in different cells."""
    sc = Scenario()
    sc.fly(100, 101, 1)
    _loadout(sc, bombs=2, rockets=0)
    sc.declare(0, 400, "Block_Test", 501, pos=Pos(2_999.0, 30.0, -2_999.0))
    _release(sc, 100, Pos(3_001.0, 900.0, -3_001.0))
    assert _a(sc).time_on_target_s == 60.0


def test_friendly_objects_do_not_count() -> None:
    sc = Scenario()
    sc.fly(100, 101, 1)
    _loadout(sc, bombs=2, rockets=0)
    sc.declare(0, 410, "Block_Test", 601, pos=TARGET)  # the bomber's own coalition
    _release(sc, 100)
    assert _a(sc).time_on_target_s == 0.0


@pytest.mark.parametrize("kind", ["Truck_Test", "Flak_Test", "Boat_Test", "M46 Patton", "Block_Test"])
def test_every_ground_class_is_a_target(kind: str) -> None:
    sc = Scenario()
    sc.fly(100, 101, 1)
    _loadout(sc, bombs=2, rockets=0)
    sc.declare(0, 400, kind, 501, pos=TARGET)
    _release(sc, 100)
    assert _a(sc).time_on_target_s == 60.0


@pytest.mark.parametrize("kind", ["MiG-15bis", "BotPlanePilot_Test", "CParachute", "Unknown_Thing"])
def test_aircraft_crew_equipment_and_unknown_objects_are_not_targets(kind: str) -> None:
    sc = Scenario()
    sc.fly(100, 101, 1)
    _loadout(sc, bombs=2, rockets=0)
    sc.declare(0, 400, kind, 501, pos=TARGET)
    _release(sc, 100)
    assert _a(sc).time_on_target_s == 0.0


def test_neutral_objects_do_not_count() -> None:
    sc = Scenario()
    sc.fly(100, 101, 1)
    _loadout(sc, bombs=2, rockets=0)
    sc.declare(0, 400, "Block_Test", 0, pos=TARGET)  # country 0 is coalition 0
    _release(sc, 100)
    assert _a(sc).time_on_target_s == 0.0


def test_destroyed_target_does_not_count_but_a_live_one_did() -> None:
    sc = _strike()
    _release(sc, 100)
    sc.kill(120, 100, 400, pos=TARGET)
    _release(sc, 140)  # the target is gone; the other enemy object is out of range
    assert _a(sc).time_on_target_s == 60.0
    sc = _strike()
    sc.kill(50, 100, 400, pos=TARGET)
    _release(sc, 100)
    assert _a(sc).time_on_target_s == 0.0


def test_target_that_appears_later_does_not_count_earlier_releases() -> None:
    sc = _strike()
    over_truck = Pos(60_000.0, 900.0, 60_000.0)
    _release(sc, 100, over_truck)
    sc.declare(200, 401, "Truck_Test", 501, pos=Pos(60_000.0, 30.0, 60_000.0))
    _release(sc, 250, over_truck)
    assert _a(sc).time_on_target_s == 60.0  # only the second release has a target; its pass starts 60 s before


def test_a_moving_target_is_where_the_last_damage_line_put_it() -> None:
    sc = _strike()
    sc.declare(0, 401, "Truck_Test", 501, pos=Pos(60_000.0, 30.0, 60_000.0))
    near_old, near_new = Pos(60_000.0, 900.0, 60_000.0), Pos(40_000.0, 900.0, 40_000.0)
    _release(sc, 20, near_old)  # before the move: counts, the truck was still there
    sc.damage(50, 100, 401, 0.2, pos=Pos(40_000.0, 30.0, 40_000.0))  # it drove 28 km
    _release(sc, 100, near_old)  # after: nothing there any more
    _release(sc, 400, near_new)
    # first attack: takeoff (5 s) to 20 s; second, more than 300 s later: 60 s
    assert _a(sc).time_on_target_s == 15.0 + 60.0


def test_lead_in_never_starts_before_the_takeoff() -> None:
    sc = _strike(up=20)
    _release(sc, 45)
    assert _a(sc).time_on_target_s == 25.0


def test_lead_in_is_not_taken_before_the_takeoff_of_a_resupplied_leg() -> None:
    sc = _strike()
    _release(sc, 100)
    sc.land(200, 100)
    sc.takeoff(500, 100)
    _release(sc, 520)
    assert _a(sc, end=700).time_on_target_s == 60.0 + 20.0


def test_air_start_lead_in_starts_at_the_spawn() -> None:
    sc = Scenario()
    sc.declare(0, 400, "Block_Test", 501, pos=TARGET)
    sc.player(10, 100, 101, 1, in_air=0)
    _loadout(sc, bombs=2, rockets=0)
    _release(sc, 40)
    assert _a(sc).time_on_target_s == 30.0


def test_releases_close_together_are_one_attack() -> None:
    sc = _strike()
    _release(sc, 100)
    _release(sc, 250)  # 150 s later, within the 300 s gap
    assert _a(sc).time_on_target_s == 210.0  # 40 s to 250 s


def test_releases_further_apart_than_the_gap_are_two_attacks() -> None:
    sc = _strike()
    _release(sc, 100)
    _release(sc, 500)  # 400 s later
    assert _a(sc).time_on_target_s == 120.0


def test_the_gap_is_a_config_value() -> None:
    sc = _strike()
    _release(sc, 100)
    _release(sc, 250)
    assert _a(sc, ReplayRules(tot_pass_gap_s=100.0)).time_on_target_s == 120.0


def test_lead_in_does_not_overlap_the_previous_attack() -> None:
    rules = ReplayRules(tot_lead_in_s=300.0, tot_pass_gap_s=100.0)
    sc = _strike()
    _release(sc, 400)
    _release(sc, 520)  # 120 s later: a new attack whose 300 s lead-in would reach back into the first one
    assert _a(sc, rules).time_on_target_s == 300.0 + 120.0  # 100 s to 400 s, then 400 s to 520 s


def test_far_releases_between_near_ones_do_not_bridge_a_gap() -> None:
    sc = _strike()
    _release(sc, 100)
    _release(sc, 300, FAR_FROM_TARGET)  # not counted, so it can't join the passes
    _release(sc, 500)
    assert _a(sc).time_on_target_s == 120.0


def test_releases_after_the_aircraft_was_lost_do_not_count() -> None:
    sc = _strike()
    _release(sc, 100)
    sc.kill(110, 400, 100)  # shot down by an enemy object
    _release(sc, 130)
    assert _a(sc).time_on_target_s == 60.0


def test_releases_after_the_sortie_end_do_not_count() -> None:
    sc = _strike()
    _release(sc, 100)
    sc.end(200, 100, 101)
    _release(sc, 250)
    assert by_acct(run(sc.events, _Catalog()), 1).time_on_target_s == 60.0
