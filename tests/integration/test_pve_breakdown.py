"""PvE breakdown counters (FR-WEB-21, TD-22): losses by class and air kills by victim, stored per sortie and summed on
all counter tables.

Invariants: on every row the death classes add up to `deaths`, the planes-lost classes to `planes_lost`, and
`kills_air_pvp + kills_air_ai == kills_air`; a rebuild gives the same rows as the incremental save."""

from datetime import timedelta

import pytest

from il2ks.core.replay.result import LOSS_CLASSES, MissionResult
from il2ks.db.models import LossClass, Player, PlayerAircraft, PlayerMission, PlayerSortie
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.counters import COUNTER_FIELDS
from tests.factories import STARTED_AT, account, meta, mission, save, sortie

pytestmark = pytest.mark.django_db


def pve_mission() -> MissionResult:
    return mission(
        (
            sortie(0, 1, kills_air=3, kills_air_pvp=1),  # 1 player + 2 AI aircraft
            sortie(1, 1, aircraft_type="Il-10", is_death=True, is_plane_lost=True, loss_class="aaa"),
            sortie(2, 1, is_death=True, is_plane_lost=True, loss_class="aaa"),
            sortie(3, 1, is_death=True, is_plane_lost=True, loss_class="ai_gunner"),
            sortie(4, 1, is_plane_lost=True, loss_class="aaa"),  # bailed out of an AAA hit: a loss, no death
            sortie(5, 1, is_death=True, is_plane_lost=True, loss_class="environment"),
            sortie(6, 2, coalition=2, aircraft_type="F-86A-5", kills_air_ai=4, is_death=True, is_plane_lost=True),
            sortie(7, 3, aircraft_type="Turret_IL10", role="gunner", is_death=True, loss_class="ground"),
            sortie(8, 4),
        )
    )


def class_counts(row: PlayerMission | Player | PlayerAircraft, family: str) -> dict[str, int]:
    return {c: getattr(row, f"{family}_by_{c}") for c in LOSS_CLASSES}


def test_sortie_stores_the_class_and_the_kill_split() -> None:
    save(pve_mission())

    assert PlayerSortie.objects.get(spawn_tick=1000).loss_class == ""  # nothing lost
    assert PlayerSortie.objects.get(spawn_tick=2000).loss_class == "aaa"
    assert PlayerSortie.objects.get(spawn_tick=7000).loss_class == "player"  # the factory default
    first = PlayerSortie.objects.get(spawn_tick=1000)
    assert (first.kills_air, first.kills_air_pvp, first.kills_air_ai) == (3, 1, 2)
    assert PlayerSortie.objects.get(spawn_tick=7000).kills_air_ai == 4


def test_classes_add_up_on_every_table() -> None:
    save(pve_mission())

    for sortie_row in PlayerSortie.objects.all():
        assert sortie_row.kills_air_pvp + sortie_row.kills_air_ai == sortie_row.kills_air
        assert (sortie_row.loss_class != "") == (sortie_row.is_plane_lost or sortie_row.is_death)
    for model in (PlayerMission, Player, PlayerAircraft):
        rows = model._default_manager.all()
        assert rows
        for row in rows:
            assert row.kills_air_pvp + row.kills_air_ai == row.kills_air, (model.__name__, row.pk)
            assert sum(class_counts(row, "deaths").values()) == row.deaths, (model.__name__, row.pk)
            assert sum(class_counts(row, "planes_lost").values()) == row.planes_lost, (model.__name__, row.pk)


def test_counters_on_player_mission_and_aircraft() -> None:
    save(pve_mission())

    # The gunner sortie (a death to a ground unit) is not counted (FR-WEB-14).
    for row in (
        PlayerMission.objects.get(player__account_uuid=account(1)),
        Player.objects.get(account_uuid=account(1)),
    ):
        assert (row.deaths, row.planes_lost) == (4, 5)
        assert class_counts(row, "deaths") == dict.fromkeys(LOSS_CLASSES, 0) | {
            "aaa": 2,
            "ai_gunner": 1,
            "environment": 1,
        }
        assert class_counts(row, "planes_lost")["aaa"] == 3
        assert (row.kills_air, row.kills_air_pvp, row.kills_air_ai) == (3, 1, 2)
    il10 = PlayerAircraft.objects.get(player__account_uuid=account(1), aircraft__log_name="Il-10")
    assert (il10.deaths, il10.deaths_by_aaa, il10.planes_lost_by_aaa) == (1, 1, 1)
    p2 = Player.objects.get(account_uuid=account(2))
    assert (p2.deaths_by_player, p2.kills_air_ai) == (1, 4)
    assert Player.objects.get(account_uuid=account(4)).deaths == 0


def test_rebuild_equals_incremental() -> None:
    save(pve_mission())
    save(
        mission((sortie(0, 1, is_death=True, is_plane_lost=True, loss_class="ground", kills_air_ai=2),)),
        meta("2026-09-20_22-00-00", STARTED_AT + timedelta(days=1)),
    )
    before = [list(m._default_manager.order_by("pk").values()) for m in (Player, PlayerAircraft, PlayerMission)]

    rebuild_aggregates()

    assert [list(m._default_manager.order_by("pk").values()) for m in (Player, PlayerAircraft, PlayerMission)] == before
    assert Player.objects.get(account_uuid=account(1)).deaths_by_ground == 1


def test_registry_and_model_choices_have_every_class() -> None:
    assert [c.value for c in LossClass] == list(LOSS_CLASSES)
    for c in LOSS_CLASSES:
        assert f"deaths_by_{c}" in COUNTER_FIELDS
        assert f"planes_lost_by_{c}" in COUNTER_FIELDS
