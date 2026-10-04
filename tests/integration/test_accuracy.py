"""Gun accuracy (doc 13 "Accuracy"): level 2 sums rounds and hits only over the sorties whose rounds fired are known,
air accuracy is read from air-superiority sorties and ground accuracy from attack sorties; the pages show them; an
upgraded database derives the sortie figures from the stored ammo JSON."""

import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path

import pytest
from django.test import Client

from il2ks.config import Config
from il2ks.db.models import AircraftStats, GameObject, Player, PlayerAircraft, PlayerSortie, SiteSettings
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.counters import COUNTER_FIELDS
from il2ks.ops import migrate
from tests.factories import account, mission, save, sortie

pytestmark = pytest.mark.django_db


def two_sorties_one_unknown() -> None:
    save(
        mission(
            (
                sortie(
                    0,
                    1,
                    combat_role="air_superiority",
                    kills_air=1,
                    rounds_fired=200,
                    gun_hits_air=10,
                    gun_hits_ground=2,
                ),
                # resupplied: the rounds are unknown, so its 50 hits must not inflate the ratio
                sortie(1, 1, combat_role="air_superiority", rounds_fired=None, gun_hits_air=50),
                sortie(
                    2, 1, combat_role="attack", kills_ground=1, rounds_fired=300, gun_hits_air=1, gun_hits_ground=30
                ),
            )
        )
    )


def test_level_2_counts_hits_only_of_sorties_with_known_rounds() -> None:
    two_sorties_one_unknown()

    p = Player.objects.get(account_uuid=account(1))

    assert (p.accuracy_rounds, p.accuracy_hits) == (500, 43)
    assert (p.accuracy_air_rounds, p.accuracy_air_hits) == (200, 10)  # air hits of air-superiority sorties
    assert (p.accuracy_ground_rounds, p.accuracy_ground_hits) == (300, 30)  # ground hits of attack sorties
    assert (p.gun_hits_air, p.gun_hits_ground) == (61, 32)  # the exact hits of every sortie


def test_rebuild_equals_incremental() -> None:
    two_sorties_one_unknown()
    save(mission((sortie(0, 1, combat_role="attack", rounds_fired=100, gun_hits_ground=7),)))

    def snapshot() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        return (
            list(Player.objects.order_by("pk").values(*COUNTER_FIELDS)),
            list(PlayerAircraft.objects.order_by("player_id", "aircraft_id").values(*COUNTER_FIELDS)),
        )

    incremental = snapshot()
    rebuild_aggregates()
    assert snapshot() == incremental


def test_profile_and_aircraft_pages_show_the_accuracy(client: Client) -> None:
    two_sorties_one_unknown()
    player = Player.objects.get(account_uuid=account(1))

    html = client.get(f"/players/{player.pk}/?tour=all").content.decode()

    assert "Air accuracy" in html
    assert "5.0%" in html  # 10 hits in 200 rounds
    assert "Ground accuracy" in html
    assert "10.0%" in html  # 30 hits in 300 rounds


def test_sortie_page_shows_accuracy_or_says_the_rounds_are_unknown(client: Client) -> None:
    two_sorties_one_unknown()
    known = PlayerSortie.objects.get(rounds_fired=200)
    unknown = PlayerSortie.objects.get(rounds_fired=None)

    html = client.get(f"/sorties/{known.pk}/").content.decode()
    assert "6.0%" in html  # 12 hits in 200 rounds
    assert "10 on aircraft, 2 on the ground, of 200 rounds" in html
    html = client.get(f"/sorties/{unknown.pk}/").content.decode()
    assert "rounds fired unknown" in html
    assert "Gun accuracy" not in html


def test_optional_columns_sort_by_accuracy(client: Client) -> None:
    save(mission((sortie(0, 1, rounds_fired=100, gun_hits_air=2), sortie(1, 2, rounds_fired=100, gun_hits_air=9))))

    html = client.get("/players/?cols=accuracy&sort=-accuracy").content.decode()

    assert "Gun accuracy" in html
    assert html.index("9.0%") < html.index("2.0%")


def test_backfill_derives_the_sortie_figures_from_the_stored_json_and_rebuilds_level_2() -> None:
    save(mission((sortie(0, 1, combat_role="attack"), sortie(1, 2, combat_role="attack"), sortie(2, 3))))
    GameObject.objects.update_or_create(log_name="B-29", defaults={"display_name": "B-29", "cls": "bomber"})
    GameObject.objects.update_or_create(log_name="M46 Patton", defaults={"display_name": "M46", "cls": "tank"})
    helper = PlayerSortie.objects.get(name_at_time="Player-2")
    flown = PlayerSortie.objects.get(name_at_time="Player-1")
    flown.ammo = {
        "used": {"bullets": 150, "shells": 0, "bombs": 0, "rockets": 0},
        "hits": [
            {"ammo": "BULLET_12-7_USA_API", "hits_given": 8},
            {"ammo": "SHELL_23_RUS_HET", "hits_given": 4},
            {"ammo": "BOMB_100kg_RUS_FAB100", "hits_given": 3},  # ordnance: not a gun hit
        ],
    }
    flown.damage_breakdown = [
        {"counterpart": {"object_type": "B-29", "sortie_id": None}, "hits_dealt": 5},
        {"counterpart": {"object_type": "MiG-15bis", "sortie_id": helper.pk}, "hits_dealt": 2},
        {"counterpart": {"object_type": "M46 Patton", "sortie_id": None}, "hits_dealt": 6},  # 3 are the bomb's
    ]
    flown.save(update_fields=["ammo", "damage_breakdown"])
    unknown = PlayerSortie.objects.get(name_at_time="Player-3")
    unknown.ammo = {
        "used": {"bullets": None, "shells": None},
        "hits": [{"ammo": "BULLET_12-7_USA_API", "hits_given": 3}],
    }
    unknown.damage_breakdown = [{"counterpart": {"object_type": "M46 Patton", "sortie_id": None}, "hits_dealt": 3}]
    unknown.save(update_fields=["ammo", "damage_breakdown"])
    PlayerSortie.objects.update(rounds_fired=None, gun_hits_air=0, gun_hits_ground=0)
    SiteSettings.objects.filter(pk=1).update(backfills_done=[])
    cfg = Config(data_dir=Path("."), server_uid=uuid.uuid4(), timezone_name="UTC")

    migrate._run_backfills(cfg, [migrate.BACKFILL_ACCURACY])  # pyright: ignore[reportPrivateUsage]

    flown.refresh_from_db()
    unknown.refresh_from_db()
    assert (flown.rounds_fired, flown.gun_hits_air, flown.gun_hits_ground) == (150, 7, 5)
    assert (unknown.rounds_fired, unknown.gun_hits_air, unknown.gun_hits_ground) == (None, 0, 3)
    player = Player.objects.get(account_uuid=account(1))
    assert (player.accuracy_rounds, player.accuracy_hits) == (150, 12)
    assert (player.accuracy_ground_rounds, player.accuracy_ground_hits) == (150, 5)
    assert migrate._already_done(migrate.BACKFILL_ACCURACY)  # pyright: ignore[reportPrivateUsage]


def test_aircraft_page_and_list_show_the_type_accuracy(client: Client) -> None:
    two_sorties_one_unknown()
    stats = AircraftStats.objects.get()

    html = client.get(f"/aircraft/{stats.aircraft_id}/").content.decode()
    assert "Gun accuracy" in html
    assert "8.6%" in html  # 43 hits in 500 rounds
    assert "5.0% air, 10.0% ground" in html

    listing = client.get("/aircraft/?cols=accuracy,accuracy_air,accuracy_ground&sort=-accuracy").content.decode()
    assert "8.6%" in listing


def test_backfill_writes_only_the_sorties_whose_figures_change(monkeypatch: pytest.MonkeyPatch) -> None:
    from il2ks.ingest import dbutil

    save(mission((sortie(0, 1), sortie(1, 2))))
    flown = PlayerSortie.objects.get(name_at_time="Player-1")
    flown.ammo = {"used": {"bullets": 40, "shells": 0, "bombs": 0, "rockets": 0}, "hits": []}
    flown.save(update_fields=["ammo"])
    PlayerSortie.objects.exclude(pk=flown.pk).update(ammo={}, damage_breakdown=[])
    PlayerSortie.objects.update(rounds_fired=None, gun_hits_air=0, gun_hits_ground=0)
    written: list[list[int]] = []
    real = dbutil.update_partial_rows

    def spy(model: type[PlayerSortie], rows: Iterable[PlayerSortie], fields: Sequence[str]) -> None:
        batch = list(rows)
        written.append([row.pk for row in batch])
        real(model, batch, fields)

    monkeypatch.setattr(dbutil, "update_partial_rows", spy)

    assert migrate._check_accuracy()  # pyright: ignore[reportPrivateUsage]

    assert written == [[flown.pk]]  # the other sortie stays (None, 0, 0): not written
    flown.refresh_from_db()
    assert flown.rounds_fired == 40
