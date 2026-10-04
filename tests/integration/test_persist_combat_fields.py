"""Ground-loss flags, combat role and time on target: stored on the sortie and counted on all three counter tables
(FR-WEB-19, FR-WEB-20)."""

import pytest

from il2ks.core.replay.result import MissionResult
from il2ks.db.models import GameObject, Player, PlayerAircraft, PlayerMission, PlayerSortie
from il2ks.ingest.aggregates import rebuild_aggregates
from tests.factories import account, mission, save, sortie

pytestmark = pytest.mark.django_db


def flagged_mission() -> MissionResult:
    return mission(
        (
            sortie(0, 1, taxi_accident=True, outcome="crashed", combat_role="air_superiority"),
            sortie(1, 1, strafed_on_ground=True, combat_role="attack", time_on_target_s=40.5),
            sortie(2, 1, aircraft_type="IL-10", combat_role="attack", time_on_target_s=19.5),
            sortie(3, 1, combat_role="air_superiority"),
            sortie(4, 1, aircraft_type="Turret_IL10", role="gunner", combat_role=None),
            sortie(5, 2, coalition=2, aircraft_type="F-86A-5"),
        )
    )


def test_sortie_fields_are_stored() -> None:
    save(flagged_mission())

    rows = {s.spawn_tick: s for s in PlayerSortie.objects.all()}
    taxi, strafed, attack, air, gunner, plain = (rows[t] for t in (1000, 2000, 3000, 4000, 5000, 6000))
    assert (taxi.taxi_accident, taxi.strafed_on_ground, taxi.combat_role, taxi.time_on_target_s) == (
        True,
        False,
        "air_superiority",
        None,
    )
    assert (strafed.strafed_on_ground, strafed.combat_role, strafed.time_on_target_s) == (True, "attack", 40.5)
    assert (attack.combat_role, attack.time_on_target_s) == ("attack", 19.5)
    assert (air.taxi_accident, air.combat_role) == (False, "air_superiority")
    assert (gunner.combat_role, gunner.time_on_target_s) == (None, None)
    assert (plain.taxi_accident, plain.strafed_on_ground, plain.combat_role) == (False, False, None)


def test_counters_on_mission_player_and_aircraft() -> None:
    save(flagged_mission())

    for row in (
        PlayerMission.objects.get(player__account_uuid=account(1)),
        Player.objects.get(account_uuid=account(1)),
    ):
        # The gunner sortie is not counted (FR-WEB-14).
        assert (row.sorties, row.taxi_accidents, row.strafed_on_ground, row.attack_sorties) == (4, 1, 1, 2)
        assert row.time_on_target_s == pytest.approx(60.0)
    mig = PlayerAircraft.objects.get(player__account_uuid=account(1), aircraft__log_name="MiG-15bis")
    assert (mig.sorties, mig.taxi_accidents, mig.strafed_on_ground, mig.attack_sorties) == (3, 1, 1, 1)
    assert mig.time_on_target_s == pytest.approx(40.5)
    il10 = PlayerAircraft.objects.get(player__account_uuid=account(1), aircraft__log_name="IL-10")
    assert (il10.attack_sorties, il10.time_on_target_s) == (1, 19.5)
    other = Player.objects.get(account_uuid=account(2))
    assert (other.taxi_accidents, other.strafed_on_ground, other.attack_sorties, other.time_on_target_s) == (
        0,
        0,
        0,
        0.0,
    )


def test_rebuild_reproduces_the_new_counters() -> None:
    save(flagged_mission())
    before = [list(m._default_manager.order_by("pk").values()) for m in (Player, PlayerAircraft, PlayerMission)]

    rebuild_aggregates()

    assert [list(m._default_manager.order_by("pk").values()) for m in (Player, PlayerAircraft, PlayerMission)] == before


def test_game_object_gets_its_propulsion_from_the_catalog() -> None:
    save(flagged_mission())

    props = dict(GameObject.objects.filter(cls__in=["fighter", "attacker"]).values_list("log_name", "propulsion"))
    assert props == {"MiG-15bis": "jet", "F-86A-5": "jet", "IL-10": "prop"}
    assert GameObject.objects.get(log_name="Turret_IL10").propulsion == ""
