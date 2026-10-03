"""Ground kills by category and the static count: stored per sortie, summed on all counter tables (OQ-33, TD-22).

Invariant: the categories add up to `kills_ground` on every sortie and in every aggregate."""

from datetime import timedelta

import pytest

from il2ks.core.catalog.loader import GROUND_CATEGORIES
from il2ks.core.replay.result import MissionResult
from il2ks.db.models import GameObject, Player, PlayerAircraft, PlayerMission, PlayerSortie
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.counters import COUNTER_FIELDS
from tests.factories import STARTED_AT, account, meta, mission, save, sortie

pytestmark = pytest.mark.django_db


def ground_mission() -> MissionResult:
    return mission(
        (
            sortie(0, 1, ground_by_category={"tank": 2, "vehicle": 3, "other": 10}, kills_ground_static=12),
            sortie(1, 1, aircraft_type="Il-10", ground_by_category={"building": 4, "parked_aircraft": 1}),
            sortie(2, 1, aircraft_type="Il-10", kills_ground=2, kills_ground_static=2),  # all "other"
            sortie(3, 2, coalition=2, aircraft_type="F-86A-5", ground_by_category={"aaa": 1, "ship": 1, "train": 1}),
            sortie(4, 3, aircraft_type="Turret_IL10", role="gunner", ground_by_category={"tank": 9}),
            sortie(5, 4, kills_air=3),
        )
    )


def breakdown(row: PlayerSortie | PlayerMission | Player | PlayerAircraft) -> dict[str, int]:
    return {c: getattr(row, f"kills_ground_{c}") for c in GROUND_CATEGORIES}


def test_sortie_stores_the_breakdown_and_static_count() -> None:
    save(ground_mission())

    first = PlayerSortie.objects.get(spawn_tick=1000)
    assert breakdown(first) == {
        "tank": 2,
        "vehicle": 3,
        "artillery": 0,
        "aaa": 0,
        "ship": 0,
        "train": 0,
        "building": 0,
        "parked_aircraft": 0,
        "other": 10,
    }
    assert (first.kills_ground, first.kills_ground_static) == (15, 12)
    assert PlayerSortie.objects.get(spawn_tick=3000).kills_ground_other == 2


def test_categories_sum_to_ground_kills_on_every_table() -> None:
    save(ground_mission())

    for model in (PlayerSortie, PlayerMission, Player, PlayerAircraft):
        rows = model._default_manager.all()
        assert rows
        for row in rows:
            assert sum(breakdown(row).values()) == row.kills_ground, (model.__name__, row.pk)
            assert row.kills_ground_static <= row.kills_ground, (model.__name__, row.pk)


def test_counters_on_player_mission_and_aircraft() -> None:
    save(ground_mission())

    # The gunner sortie (9 tank kills) is not counted, like the other counters (FR-WEB-14).
    for row in (
        PlayerMission.objects.get(player__account_uuid=account(1)),
        Player.objects.get(account_uuid=account(1)),
    ):
        assert row.kills_ground == 22
        assert (row.kills_ground_tank, row.kills_ground_vehicle, row.kills_ground_building) == (2, 3, 4)
        assert (row.kills_ground_parked_aircraft, row.kills_ground_other, row.kills_ground_static) == (1, 12, 14)
    mig = PlayerAircraft.objects.get(player__account_uuid=account(1), aircraft__log_name="MiG-15bis")
    assert (mig.kills_ground, mig.kills_ground_other, mig.kills_ground_static) == (15, 10, 12)
    il10 = PlayerAircraft.objects.get(player__account_uuid=account(1), aircraft__log_name="Il-10")
    assert (il10.kills_ground, il10.kills_ground_building, il10.kills_ground_other) == (7, 4, 2)
    assert Player.objects.get(account_uuid=account(4)).kills_ground == 0


def test_rebuild_equals_incremental() -> None:
    save(ground_mission())
    save(
        mission((sortie(0, 1, ground_by_category={"train": 5}, kills_ground_static=5),)),
        meta("2026-09-20_22-00-00", STARTED_AT + timedelta(days=1)),
    )
    before = [list(m._default_manager.order_by("pk").values()) for m in (Player, PlayerAircraft, PlayerMission)]

    rebuild_aggregates()

    assert [list(m._default_manager.order_by("pk").values()) for m in (Player, PlayerAircraft, PlayerMission)] == before
    assert Player.objects.get(account_uuid=account(1)).kills_ground_train == 5


def test_counter_registry_has_every_category() -> None:
    for c in GROUND_CATEGORIES:
        assert f"kills_ground_{c}" in COUNTER_FIELDS
    assert "kills_ground_static" in COUNTER_FIELDS


def test_game_object_gets_its_ground_category_from_the_catalog() -> None:
    save(
        mission(
            (sortie(0, 1),),
            extra_types=frozenset({"GAZ_63", "Fence wire 5m", "M46 Patton"}),
        )
    )

    categories = dict(GameObject.objects.values_list("log_name", "ground_category"))
    assert categories["GAZ_63"] == "vehicle"  # static, but a vehicle
    assert categories["Fence wire 5m"] == "other"
    assert categories["M46 Patton"] == "tank"
    assert categories["MiG-15bis"] == ""
