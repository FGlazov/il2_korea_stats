"""Favourite loadout per player and aircraft type (`PlayerAircraftBuild`, FR-WEB-4, OQ-117): level 2 incremental ==
rebuild, tour scoping, hidden rules, the profile section (the favourite loadout only) and its query budget."""

import uuid
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from django.test import Client

from il2ks.config import Config
from il2ks.core.replay.result import SortieResult
from il2ks.db.models import BuildKind, Player, PlayerAircraftBuild, SiteSettings, Tour
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ops import migrate
from il2ks.queries.builds import player_builds
from tests.factories import STARTED_AT, account, meta, mission, rows, save, sortie
from tests.simple_reads import PROFILE_READS_ALL_TIME, assert_simple_reads

pytestmark = pytest.mark.django_db


def flown(index: int, player: int, payload: int, mods: int) -> SortieResult:
    return replace(sortie(index, player, payload_id=payload), weapon_mods=mods)


def seed() -> None:
    save(
        mission(
            (
                flown(0, 1, 1, 5),
                flown(1, 1, 1, 5),
                flown(2, 1, 2, 0),
                flown(3, 2, 7, 0),
            )
        ),
        meta("2026-09-19_22-34-13", STARTED_AT),
    )
    save(
        mission((flown(0, 1, 2, 0),)),
        meta("2026-09-21_22-34-13", STARTED_AT + timedelta(days=2)),
    )


def player_pk(n: int) -> int:
    return Player.objects.get(account_uuid=account(n)).pk


def mig_rows(player: int, tour: Tour | None = None) -> dict[tuple[int, str], int]:
    found = PlayerAircraftBuild.objects.filter(player_id=player_pk(player), aircraft__log_name="MiG-15bis", tour=tour)
    return {(r.value, r.label): r.sorties for r in found}


def test_only_the_loadout_is_tallied_all_time() -> None:
    seed()

    assert mig_rows(1) == {(1, "Payload 1"): 2, (2, "Payload 2"): 2}
    assert set(PlayerAircraftBuild.objects.values_list("kind", flat=True)) == {BuildKind.PAYLOAD.value}


def test_tour_scope_follows_the_tour_rows() -> None:
    seed()

    tour_rows = PlayerAircraftBuild.objects.filter(tour__isnull=False)
    assert tour_rows
    assert sum(r.sorties for r in tour_rows) == 5  # every counted sortie of the seed lies in exactly one tour
    assert PlayerAircraftBuild.objects.filter(tour__isnull=True).count() == 3


def test_incremental_equals_rebuild() -> None:
    seed()
    before = rows(PlayerAircraftBuild)
    assert before

    rebuild_aggregates()

    def bare(found: list[dict[str, object]]) -> list[dict[str, object]]:
        return [{k: v for k, v in r.items() if k != "id"} for r in found]

    assert bare(rows(PlayerAircraftBuild)) == bare(before)


def test_resaving_a_mission_does_not_double_count() -> None:
    seed()
    before = mig_rows(1)

    save(
        mission((flown(0, 1, 2, 0),)),
        meta("2026-09-21_22-34-13", STARTED_AT + timedelta(days=2)),
    )

    assert mig_rows(1) == before


def test_reads_shares_and_favourite() -> None:
    seed()

    builds = player_builds(Player.objects.get(account_uuid=account(1)))
    (build,) = builds.values()

    assert build.sorties == 4
    assert [(p.payload_id, p.percent) for p in build.loadouts] == [(1, 50), (2, 50)]
    assert build.loadouts[0].payload_id == 1  # the favourite; ties: the lower id first


def test_profile_shows_the_favourite_loadout_only(client: Client) -> None:
    seed()

    body = client.get(f"/players/{player_pk(1)}/?tour=all").content.decode()

    assert "Favourite loadout" in body
    assert "Payload 1" in body
    assert "50%" in body
    assert "build-detail" not in body
    for gone in ("Hits by ammo", "Weapon modifications", "not the belt they chose", "Anti-G"):
        assert gone not in body


def test_unknown_payload_shows_the_raw_id(client: Client) -> None:
    save(mission((flown(0, 1, -1, 0),)))  # FakeCatalog: no name for a negative id

    body = client.get(f"/players/{player_pk(1)}/?tour=all").content.decode()

    assert "Unknown payload (id -1)" in body


def test_hidden_player_has_no_page(client: Client) -> None:
    seed()
    Player.objects.filter(account_uuid=account(1)).update(is_hidden=True)

    assert client.get(f"/players/{player_pk(1)}/?tour=all").status_code == 404


def test_profile_budget_with_builds(client: Client) -> None:
    seed()

    assert_simple_reads(client, f"/players/{player_pk(1)}/?tour=all", max_queries=PROFILE_READS_ALL_TIME)


def test_rebuild_drops_leftover_mod_and_ammo_rows() -> None:
    """A database from before OQ-117 can still hold `mods` / `ammo` rows: the next recompute removes them."""
    seed()
    good = rows(PlayerAircraftBuild)
    one = PlayerAircraftBuild.objects.first()
    assert one is not None
    PlayerAircraftBuild.objects.create(
        player=one.player, aircraft=one.aircraft, tour=None, kind="mods", value=5, label="", sorties=2
    )

    rebuild_aggregates()

    assert [{k: v for k, v in r.items() if k != "id"} for r in rows(PlayerAircraftBuild)] == [
        {k: v for k, v in r.items() if k != "id"} for r in good
    ]


def test_upgrade_backfill_builds_the_loadouts_of_an_old_database() -> None:
    seed()
    expected = [{k: v for k, v in r.items() if k != "id"} for r in rows(PlayerAircraftBuild)]
    PlayerAircraftBuild.objects.all().delete()
    SiteSettings.objects.filter(pk=1).update(backfills_done=[])
    cfg = Config(data_dir=Path("."), server_uid=uuid.uuid4(), timezone_name="UTC")

    migrate._run_backfills(cfg, [migrate.BACKFILL_BUILDS])  # pyright: ignore[reportPrivateUsage]

    assert [{k: v for k, v in r.items() if k != "id"} for r in rows(PlayerAircraftBuild)] == expected
