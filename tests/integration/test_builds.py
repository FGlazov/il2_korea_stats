"""Favourite loadout, weapon mods and gun ammo mix per player and aircraft type (`PlayerAircraftBuild`, FR-WEB-4):
level 2 incremental == rebuild, tour scoping, hidden rules, the profile section and its query budget."""

import uuid
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from django.test import Client

from il2ks.config import Config
from il2ks.core.replay.result import AmmoHits, SortieResult
from il2ks.db.models import BuildKind, Player, PlayerAircraftBuild, SiteSettings, SortieGunHits, Tour
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ops import migrate
from il2ks.queries.builds import player_builds
from tests.factories import STARTED_AT, account, meta, mission, rows, save, sortie
from tests.simple_reads import PROFILE_READS_ALL_TIME, assert_simple_reads

pytestmark = pytest.mark.django_db

API = "BULLET_12-7_USA_API"
APIT = "BULLET_12-7_USA_APIT"


def flown(index: int, player: int, payload: int, mods: int, *hits: AmmoHits) -> SortieResult:
    return replace(sortie(index, player, payload_id=payload), weapon_mods=mods, ammo_hits=hits)


def seed() -> None:
    save(
        mission(
            (
                flown(0, 1, 1, 5, AmmoHits(API, hits_given=30), AmmoHits("BOMB_449kg_USA_M65", hits_given=4)),
                flown(1, 1, 1, 5, AmmoHits(API, hits_given=10), AmmoHits(APIT, hits_given=60)),
                flown(2, 1, 2, 0),
                flown(3, 2, 7, 0, AmmoHits(API, hits_given=1)),
            )
        ),
        meta("2026-09-19_22-34-13", STARTED_AT),
    )
    save(
        mission((flown(0, 1, 2, 0, AmmoHits(API, hits_given=5)),)),
        meta("2026-09-21_22-34-13", STARTED_AT + timedelta(days=2)),
    )


def player_pk(n: int) -> int:
    return Player.objects.get(account_uuid=account(n)).pk


def mig_rows(player: int, kind: str, tour: Tour | None = None) -> dict[tuple[int, str], tuple[int, int]]:
    found = PlayerAircraftBuild.objects.filter(
        player_id=player_pk(player), aircraft__log_name="MiG-15bis", kind=kind, tour=tour
    )
    return {(r.value, r.label): (r.sorties, r.hits) for r in found}


def test_tallies_per_kind_all_time() -> None:
    seed()

    assert mig_rows(1, BuildKind.PAYLOAD) == {(1, "Payload 1"): (2, 0), (2, "Payload 2"): (2, 0)}
    assert mig_rows(1, BuildKind.MODS) == {(5, ""): (2, 0), (0, ""): (2, 0)}
    # gun ammo only (the bomb line is not counted), hits and the sorties that hit
    assert mig_rows(1, BuildKind.AMMO) == {(0, API): (3, 45), (0, APIT): (1, 60)}


def test_tour_scope_follows_the_tour_rows() -> None:
    seed()

    tour_rows = PlayerAircraftBuild.objects.filter(tour__isnull=False, kind=BuildKind.PAYLOAD)
    assert tour_rows
    assert sum(r.sorties for r in tour_rows) == 5  # every counted sortie of the seed lies in exactly one tour
    assert PlayerAircraftBuild.objects.filter(tour__isnull=True, kind=BuildKind.PAYLOAD).count() == 3


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
    before = mig_rows(1, BuildKind.PAYLOAD)

    save(
        mission((flown(0, 1, 2, 0, AmmoHits(API, hits_given=5)),)),
        meta("2026-09-21_22-34-13", STARTED_AT + timedelta(days=2)),
    )

    assert mig_rows(1, BuildKind.PAYLOAD) == before


def test_reads_shares_and_favourite() -> None:
    seed()

    builds = player_builds(Player.objects.get(account_uuid=account(1)))
    (build,) = builds.values()

    assert build.sorties == 4
    assert [(p.payload_id, p.percent) for p in build.loadouts] == [(1, 50), (2, 50)]
    assert build.loadouts[0].payload_id == 1  # the favourite; ties: the lower id first
    assert [(a.name, a.percent) for a in build.ammo] == [(".50 BMG API-T", 57), (".50 BMG API", 43)]


def test_profile_shows_favourite_loadout_and_honest_labels(client: Client) -> None:
    seed()

    body = client.get(f"/players/{player_pk(1)}/?tour=all").content.decode()

    assert "Favourite loadout" in body
    assert "Payload 1" in body
    assert "50%" in body
    assert "Hits by ammo" in body
    assert "not the belt they chose" in body
    assert "Modification names are not known yet" not in body
    assert "No modifications" in body


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


def test_gun_hits_are_level_one_rows_without_ordnance_and_replaced_on_resave() -> None:
    seed()

    found = list(SortieGunHits.objects.values_list("ammo", flat=True))
    assert not any(ammo.startswith("BOMB") for ammo in found)
    assert len(found) == 5  # mission 1: API, API + APIT, player 2 API; mission 2: API
    count = len(found)

    save(
        mission((flown(0, 1, 2, 0, AmmoHits(API, hits_given=5)),)),
        meta("2026-09-21_22-34-13", STARTED_AT + timedelta(days=2)),
    )

    assert SortieGunHits.objects.count() == count


def test_upgrade_backfill_fills_gun_hits_and_builds_from_the_stored_ammo() -> None:
    seed()
    expected = [{k: v for k, v in r.items() if k != "id"} for r in rows(PlayerAircraftBuild)]
    gun = sorted(SortieGunHits.objects.values_list("sortie_id", "ammo", "hits"))
    SortieGunHits.objects.all().delete()
    PlayerAircraftBuild.objects.all().delete()
    SiteSettings.objects.filter(pk=1).update(backfills_done=[])
    cfg = Config(data_dir=Path("."), server_uid=uuid.uuid4(), timezone_name="UTC")

    migrate._run_backfills(cfg, [migrate.BACKFILL_BUILDS])  # pyright: ignore[reportPrivateUsage]

    assert sorted(SortieGunHits.objects.values_list("sortie_id", "ammo", "hits")) == gun
    assert [{k: v for k, v in r.items() if k != "id"} for r in rows(PlayerAircraftBuild)] == expected
