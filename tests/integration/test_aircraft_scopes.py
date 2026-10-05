"""Every section of the aircraft page follows the tour, role and modification filter (FR-WEB-8, FR-WEB-18; maintainer
2026-10-04): the scoped level-2 rows (loadouts and mods per tour, matchups per side's role / mods, top pilots per scope,
hits to destroy and ammo mixes per the destroyed aircraft's sortie), incremental == rebuild, and the page."""

from dataclasses import replace
from datetime import timedelta

import pytest
from django.db import models
from django.test import Client, override_settings
from django.urls import reverse

from il2ks.config import LeaderboardConfig
from il2ks.core.replay.result import SingleAttackerKill
from il2ks.db.models import (
    AircraftAmmoMixStats,
    AircraftAmmoStats,
    AircraftMatchup,
    AircraftMods,
    AircraftPayload,
    AircraftRole,
    GameObject,
    MissionAircraftAmmo,
    PlayerAircraftScope,
    Tour,
    TourAircraftStats,
)
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.queries.ammo import aircraft_ammo
from tests.aircraft_pages import all_loadouts
from tests.factories import STARTED_AT, kill, meta, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("list_every_row")]

AIR = "air_superiority"
ATTACK = "attack"
OCTOBER = STARTED_AT + timedelta(days=40)
API = "BULLET_12-7_USA_API"
INC = "BULLET_12-7_USA_INC"
SOME = LeaderboardConfig(
    min_sorties=1,
    min_elo_games=1,
    min_attack_sorties=1,
    min_time_on_target_minutes=1.0,
    min_air_superiority_sorties=1,
    min_air_superiority_minutes=1.0,
)

# MiG-15bis mods (`weapon_mods.csv`): significant 1 (NR-23 cannons), 2 (air brakes), 5 (Anti-G suit): pattern [1][2][5].
BASE = 1
NR23_ANTI_G = BASE | 1 << 1 | 1 << 5
ANTI_G = BASE | 1 << 5


def shot(victim_index: int | None, *hits: tuple[str, int]) -> SingleAttackerKill:
    return SingleAttackerKill("MiG-15bis", tuple(sorted(hits)), victim_index)


def history() -> tuple[Tour, Tour]:
    """September: MiG pilot 1 (air, Anti-G suit) shoots down Sabre pilot 5; Sabre 5 shoots down MiG pilot 2 (attack,
    NR-23 and Anti-G suit); MiG pilot 4 (air, no mods) is shot down by an AI aircraft. Single-attacker kills of MiGs:
    the attack sortie (3 API hits), the unmodified air sortie (5 API + 1 INC hits), and an AI MiG (2 API). October:
    Sabre 5 shoots down MiG pilot 1 (air, Anti-G suit; 4 API hits)."""
    save(
        replace(
            mission(
                (
                    sortie(0, 1, kills_air=1, kills_air_pvp=1, combat_role=AIR, weapon_mods=ANTI_G, payload_id=1),
                    sortie(
                        1,
                        2,
                        combat_role=ATTACK,
                        weapon_mods=NR23_ANTI_G,
                        payload_id=2,
                        ground_by_category={"tank": 2},
                        time_on_target_s=600.0,
                        is_death=True,
                        is_plane_lost=True,
                    ),
                    sortie(2, 4, combat_role=AIR, weapon_mods=BASE, payload_id=1, is_death=True, is_plane_lost=True),
                    sortie(
                        3,
                        5,
                        aircraft_type="F-86A-5",
                        coalition=2,
                        combat_role=AIR,
                        kills_air=1,
                        kills_air_pvp=1,
                        is_death=True,
                    ),
                ),
                (
                    kill(100, 0, 3, victim_type="F-86A-5"),
                    kill(200, 3, 1, killer_type="F-86A-5", victim_type="MiG-15bis"),
                ),
            ),
            single_attacker_kills=(shot(1, (API, 3)), shot(2, (API, 5), (INC, 1)), shot(None, (API, 2))),
        )
    )
    save(
        replace(
            mission(
                (
                    sortie(0, 1, combat_role=AIR, weapon_mods=ANTI_G, payload_id=1, is_death=True, is_plane_lost=True),
                    sortie(1, 5, aircraft_type="F-86A-5", coalition=2, combat_role=AIR, kills_air=1, kills_air_pvp=1),
                ),
                (kill(100, 1, 0, killer_type="F-86A-5", victim_type="MiG-15bis"),),
            ),
            single_attacker_kills=(shot(0, (API, 4)),),
        ),
        meta("2026-10-29_22-00-00", OCTOBER),
    )
    september, october = Tour.objects.order_by("started_at")
    return september, october


def mig() -> GameObject:
    return GameObject.objects.get(log_name="MiG-15bis")


def snapshot() -> dict[str, list[dict[str, object]]]:
    def rows(model: type[models.Model]) -> list[dict[str, object]]:
        found = sorted(
            (dict(row) for row in model.objects.values()), key=lambda row: [str(v) for k, v in row.items() if k != "id"]
        )
        for row in found:
            row.pop("id")
        return found

    return {
        model.__name__: rows(model)
        for model in (
            AircraftPayload,
            AircraftMods,
            AircraftMatchup,
            PlayerAircraftScope,
            AircraftAmmoStats,
            AircraftAmmoMixStats,
            TourAircraftStats,
        )
    }


def hits(tour: Tour | None, role: str = "all", pattern: str = "") -> tuple[int, int] | None:
    total = aircraft_ammo(mig(), tour, role, pattern)  # type: ignore[arg-type]
    return None if total.total is None else (total.total.kills, total.total.hits)


def test_victim_role_and_mods_are_stored_on_the_level_one_ammo_rows() -> None:
    history()

    stored = {
        (r.combat_role, r.weapon_mods, r.ammo, r.kills, r.hits)
        for r in MissionAircraftAmmo.objects.filter(mission__tour__isnull=False, ammo="*")
    }
    # the AI victim has no sortie: no role, no recorded mods (it is in no filter pattern)
    assert stored == {
        (ATTACK, NR23_ANTI_G, "*", 1, 3),
        (AIR, BASE, "*", 1, 6),
        ("", -1, "*", 1, 2),
        (AIR, ANTI_G, "*", 1, 4),
    }


def test_hits_to_destroy_follow_tour_role_and_mods_of_the_destroyed_aircraft() -> None:
    september, october = history()

    assert hits(None) == (4, 15)  # all time, every role, no filter: the AI MiG counts
    assert hits(september) == (3, 11)
    assert hits(october) == (1, 4)
    assert hits(None, AIR) == (2, 10)
    assert hits(None, ATTACK) == (1, 3)
    assert hits(september, AIR) == (1, 6)
    assert hits(october, ATTACK) is None  # no empty scopes
    assert hits(None, "all", "**+") == (2, 7)  # Anti-G suit: the attack sortie and the October one
    assert hits(None, "all", "**-") == (1, 6)  # without the suit: the unmodified air sortie; the AI MiG is in neither
    assert hits(None, ATTACK, "+**") == (1, 3)
    assert hits(september, AIR, "**+") is None


def test_ammo_mixes_follow_the_scope_too() -> None:
    september, _ = history()

    both = aircraft_ammo(mig(), september, AIR, "**-")  # type: ignore[arg-type]
    assert [(m.ammos, m.instances, m.total_hits) for m in both.mixes] == [((API, INC), 1, 6)]
    attack = aircraft_ammo(mig(), None, ATTACK, "")  # type: ignore[arg-type]
    assert [(m.ammos, m.instances, m.total_hits) for m in attack.mixes] == [((API,), 1, 3)]
    assert AircraftAmmoMixStats.objects.filter(tour=None, role="all", mod_pattern="", ammo="*").count() == 2


def test_loadouts_and_mods_exist_per_tour() -> None:
    september, october = history()

    def sorties(model: type[AircraftPayload | AircraftMods], tour: Tour | None) -> int:
        rows = model.objects.filter(aircraft=mig(), tour=tour, mod_pattern="")
        return sum(r.sorties for r in rows)

    assert [sorties(AircraftPayload, t) for t in (None, september, october)] == [4, 3, 1]
    assert [sorties(AircraftMods, t) for t in (None, september, october)] == [4, 3, 1]
    assert not AircraftPayload.objects.filter(aircraft=mig(), tour=october, combat_role=ATTACK).exists()
    anti_g = AircraftMods.objects.filter(aircraft=mig(), tour=october, mod_pattern="**+")
    assert [(r.weapon_mods, r.sorties, r.deaths) for r in anti_g] == [(ANTI_G, 1, 1)]


def test_matchups_are_scoped_by_the_role_and_mods_of_each_side() -> None:
    history()
    sabre = GameObject.objects.get(log_name="F-86A-5")

    def kills(killer: GameObject, victim: GameObject, side: str, role: str, pattern: str) -> int:
        found = AircraftMatchup.objects.filter(
            killer_aircraft=killer,
            victim_aircraft=victim,
            tour=None,
            intercept=False,
            scoped_side=side,
            combat_role=role,
            mod_pattern=pattern,
        ).first()
        return 0 if found is None else found.kills

    # the MiG as the killer: one air sortie with the Anti-G suit shot a Sabre down
    assert kills(mig(), sabre, "", "all", "") == 1
    assert kills(mig(), sabre, "killer", AIR, "") == 1
    assert kills(mig(), sabre, "killer", ATTACK, "") == 0
    assert kills(mig(), sabre, "killer", "all", "**+") == 1
    assert kills(mig(), sabre, "killer", "all", "**-") == 0
    # the MiG as the victim: an attack sortie (NR-23 + suit) in September, an air sortie (suit) in October
    assert kills(sabre, mig(), "", "all", "") == 2
    assert kills(sabre, mig(), "victim", ATTACK, "") == 1
    assert kills(sabre, mig(), "victim", AIR, "") == 1
    assert kills(sabre, mig(), "victim", "all", "**+") == 2
    assert kills(sabre, mig(), "victim", "all", "+**") == 1  # NR-23 cannons: the attack sortie
    assert kills(sabre, mig(), "victim", "all", "-**") == 1  # without them: the October sortie
    assert kills(sabre, mig(), "victim", AIR, "**+") == 1
    assert kills(sabre, mig(), "victim", ATTACK, "**-") == 0
    # the Sabre has no significant mods: its side never has pattern rows
    assert (
        not AircraftMatchup.objects.filter(scoped_side="killer", killer_aircraft=sabre).exclude(mod_pattern="").exists()
    )


def test_player_scope_rows_hold_a_pilots_counters_per_scope() -> None:
    september, october = history()

    def row(player: int, tour: Tour | None, role: str, pattern: str = "") -> PlayerAircraftScope | None:
        return PlayerAircraftScope.objects.filter(
            aircraft=mig(),
            player__account_uuid__endswith=f"{player:012d}",
            tour=tour,
            role=role,
            mod_pattern=pattern,
        ).first()

    # player 1 flew the type in both tours; the all-time, every-role, unfiltered scope is `PlayerAircraft`, no row
    assert row(1, None, "all") is None
    assert (row(1, None, AIR) or PlayerAircraftScope()).sorties == 2
    assert (row(1, september, "all") or PlayerAircraftScope()).sorties == 1
    assert (row(1, october, "all") or PlayerAircraftScope()).deaths == 1
    assert (row(1, None, "all", "**+") or PlayerAircraftScope()).sorties == 2  # with the Anti-G suit, both tours
    assert (row(2, None, ATTACK) or PlayerAircraftScope()).attack_sorties == 1
    assert row(2, None, AIR) is None
    assert row(4, None, "all", "**+") is None  # flew without the suit only


def test_incremental_equals_rebuild_for_every_scope_with_a_reingest() -> None:
    september, _ = history()
    # re-ingest the September mission with different sorties, victims and hits: stale scope rows must go away
    save(
        replace(
            mission(
                (
                    sortie(0, 1, kills_air=1, kills_air_pvp=1, combat_role=ATTACK, weapon_mods=BASE, payload_id=2),
                    sortie(1, 5, aircraft_type="F-86A-5", coalition=2, combat_role=AIR, is_death=True),
                ),
                (kill(100, 0, 1, victim_type="F-86A-5"),),
            ),
            single_attacker_kills=(shot(0, (INC, 9)),),
        )
    )
    incremental = snapshot()

    rebuild_aggregates()

    assert snapshot() == incremental
    assert hits(september, ATTACK, "**-") == (1, 9)
    assert hits(september, AIR) is None


@override_settings(IL2KS_LEADERBOARDS=SOME)
def test_page_sections_all_follow_the_filters(client: Client) -> None:
    _, october = history()
    url = reverse("web:aircraft-detail", args=[mig().pk])

    def page(query: str) -> dict[str, object]:
        response = client.get(f"{url}?{query}")
        assert response.status_code == 200
        body = response.content.decode()
        for note in ("not filtered", "still cover every modification", "does not apply to matchups"):
            assert note not in body
        return {
            "tile": response.context["tile"].sorties,
            "loadouts": sum(row.payload.sorties for row in all_loadouts(client, f"{url}?{query}")),
            "mods": sum(row.stats.sorties for row in response.context["mod_sets"]),
            "matchups": [(m.kills, m.losses) for m in response.context["matchups"].rows],
            "hits": response.context["hits"].kills,
            "elo": [row.player.pk for row in response.context["elo_pilots"]],
            "ground": [row.player.pk for row in response.context["ground_pilots"]],
        }

    everything = page("tour=all")
    assert (everything["tile"], everything["loadouts"], everything["mods"], everything["hits"]) == (4, 4, 4, "4")
    assert everything["matchups"] == [(1, 2)]
    in_october = page(f"tour={october.pk}")
    assert (in_october["tile"], in_october["loadouts"], in_october["mods"], in_october["hits"]) == (1, 1, 1, "1")
    assert in_october["matchups"] == [(0, 1)]
    attack = page("tour=all&role=attack")
    assert (attack["tile"], attack["loadouts"], attack["mods"], attack["hits"]) == (1, 1, 1, "1")
    assert attack["matchups"] == [(0, 1)]
    assert attack["elo"] == []  # Elo is of air superiority fights
    assert len(attack["ground"]) == 1  # type: ignore[arg-type]
    with_suit = page("tour=all&mod5=with")
    assert (with_suit["tile"], with_suit["loadouts"], with_suit["mods"], with_suit["hits"]) == (3, 3, 3, "2")
    assert with_suit["matchups"] == [(1, 2)]
    without_suit = page("tour=all&mod5=without")
    assert (without_suit["tile"], without_suit["hits"]) == (1, "1")
    assert without_suit["matchups"] == []  # the unmodified sortie was shot down by an AI aircraft
    air_suit_october = page(f"tour={october.pk}&role=air_superiority&mod5=with")
    assert (air_suit_october["tile"], air_suit_october["hits"]) == (1, "1")


def test_a_scoped_self_pairing_takes_kills_from_the_killer_row_and_losses_from_the_victim_row() -> None:
    """Two Sabres meeting: with a role filter both the killer-scoped and the victim-scoped row of the pair match the
    type's page, and each must feed only its own column (the last row used to win both)."""
    sabre = GameObject.objects.create(log_name="F-86F-30", display_name="F-86F", cls="fighter")
    for side, kills in (("killer", 5), ("victim", 2)):
        AircraftMatchup.objects.create(
            killer_aircraft=sabre, victim_aircraft=sabre, scoped_side=side, combat_role=AIR, kills=kills
        )

    from il2ks.queries.aircraft import matchups

    table = matchups(sabre, role=AircraftRole.AIR_SUPERIORITY)

    assert [(m.enemy.pk, m.kills, m.losses) for m in table.rows] == [(sabre.pk, 5, 2)]


@override_settings(IL2KS_LEADERBOARDS=SOME)
def test_a_populated_scoped_page_stays_within_the_query_budget(client: Client) -> None:
    """The budget tests of the other aircraft files run over fixtures without pilots in scope, which skips queries (the
    top pilots' Elo read). Here every section has data: pilots in scope, ammo rows, matchups, loadouts and mods."""
    _, october = history()
    url = reverse("web:aircraft-detail", args=[mig().pk])
    scoped = f"{url}?tour={october.pk}&role=air_superiority&mod5=with&intercept=1"

    response = client.get(scoped)
    assert response.context["elo_pilots"]  # pilots in scope
    assert response.context["loadouts"]
    assert response.context["mod_sets"]
    assert response.context["hits"].by_ammo
    assert response.context["matchups"].rows

    # Budget with data in every section: the 2 of the context processor, the tours, the type's all-time row, the scope's
    # row, hits, mixes, matchups, loadouts, mod sets and one pilot board (Elo or ground) = 11.
    assert_simple_reads(client, scoped, max_queries=11)
    assert_simple_reads(client, f"{url}?tour=all&role=attack&mod5=with", max_queries=11)
    assert_simple_reads(
        client, f"{url}?tour={october.pk}&mod5=with", max_queries=12
    )  # both pilot boards: one more than a single role


@override_settings(IL2KS_LEADERBOARDS=SOME)
def test_the_attack_role_has_no_intercept_toggle_and_ignores_a_stale_intercept_link(client: Client) -> None:
    """An intercept fight is air superiority against air superiority, so with the attack role the intercept table is
    always empty: the toggle is not offered, and a stale `?intercept=1` shows the usual matchups instead of nothing."""
    history()
    url = reverse("web:aircraft-detail", args=[mig().pk])

    attack = client.get(f"{url}?tour=all&role=attack&intercept=1")
    air = client.get(f"{url}?tour=all&role=air_superiority")

    assert "Intercept sorties only" not in attack.content.decode()
    assert attack.context["intercept"] is False
    assert [(m.kills, m.losses) for m in attack.context["matchups"].rows] == [(0, 1)]
    assert "Intercept sorties only" in air.content.decode()
