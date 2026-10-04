"""The ammo breakdown and PvE breakdown pages (FR-WEB-18, FR-WEB-21, FR-ADM-3, TD-22). Synthetic data only."""

from dataclasses import replace

import pytest
from django.test import Client

from il2ks.core.replay.result import (
    UNATTRIBUTED_ORDNANCE,
    AmmoHits,
    MissionResult,
    OrdnanceUse,
    SingleAttackerKill,
    UnattributedDamage,
)
from il2ks.db.models import Mission, Player, PlayerSortie
from tests.factories import mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

API = "BULLET_12-7_USA_API"
INC = "BULLET_12-7_USA_INC"


def seed() -> MissionResult:
    """Player 1 flies a ground-attack sortie with guns and bombs and kills 1 player + 2 AI aircraft; player 2 is lost to
    anti-aircraft fire, player 3 to nobody (own doing)."""
    attacker = replace(
        sortie(
            0, 1, name="Alpha", kills_air=3, kills_air_pvp=1, kills_air_ai=2, ground_by_category={"aaa": 2, "tank": 1}
        ),
        ammo_hits=(
            AmmoHits(API, hits_given=9, hits_received=2, damage_dealt=0.8, damage_taken=0.3),
            AmmoHits("BOMB_238kg_USA_M64", hits_given=1),
        ),
        ordnance=(
            OrdnanceUse("M64", released=2, detonations=5, targets_damaged=3, kills=1, damage_dealt=2.5, direct_hits=1),
            OrdnanceUse(UNATTRIBUTED_ORDNANCE, damage_dealt=0.4),
        ),
        ammo_unattributed=UnattributedDamage(dealt=0.25, taken=0.5),
    )
    flak = sortie(1, 2, name="Bravo", is_death=True, is_plane_lost=True, outcome="shot_down", loss_class="aaa")
    crash = sortie(2, 3, name="Charlie", is_death=True, is_plane_lost=True, outcome="crashed", loss_class="environment")
    result = mission((attacker, flak, crash))
    return replace(
        result,
        single_attacker_kills=(
            SingleAttackerKill("MiG-15bis", ((API, 3), (INC, 1))),
            SingleAttackerKill("MiG-15bis", ((API, 5),)),
        ),
    )


def pk_of(player: int) -> int:
    return PlayerSortie.objects.get(player__account_uuid__endswith=f"{player:012d}").pk


def player_pk(n: int) -> int:
    return Player.objects.get(account_uuid__endswith=f"{n:012d}").pk


# --- sortie page: ammo ---
def test_sortie_page_lists_hits_by_plain_name_without_damage_and_the_ordnance_apart(client: Client) -> None:
    save(seed())

    body = client.get(f"/sorties/{pk_of(1)}/").content.decode()

    assert "Damage attributed to this ammunition" not in body  # OQ-52: the per-ammo damage is hidden, hits stay
    assert "Hits given" in body
    assert "BULLET_12-7_USA_API" not in body
    assert ">.50 BMG API<" in body
    assert 'title="M8 API"' in body  # the real designation as a tooltip
    assert "0.80" not in body
    assert "Bombs, rockets and napalm" in body
    assert "M64 500 lb General Purpose bomb" in body
    assert "Unattributed" in body
    assert "explosion" not in body.lower()
    assert "Damage no hit could be blamed on" not in body


def test_sortie_page_without_ordnance_has_no_ordnance_section(client: Client) -> None:
    save(seed())

    body = client.get(f"/sorties/{pk_of(2)}/").content.decode()

    assert "Bombs, rockets and napalm" not in body


# --- sortie page: PvE ---
def test_sortie_page_says_who_the_loss_is_owed_to(client: Client) -> None:
    save(seed())

    flak = client.get(f"/sorties/{pk_of(2)}/").content.decode()
    crash = client.get(f"/sorties/{pk_of(3)}/").content.decode()
    survivor = client.get(f"/sorties/{pk_of(1)}/").content.decode()

    assert "Lost to" in flak
    assert "Anti-aircraft" in flak
    assert "No attacker (crash, terrain, accident)" in crash
    assert "Lost to" not in survivor
    assert "1 player aircraft, 2 AI aircraft" in survivor


def test_sortie_page_budget_is_unchanged_by_the_breakdowns(client: Client) -> None:
    save(seed())

    assert_simple_reads(client, f"/sorties/{pk_of(1)}/", max_queries=2 + 5)


def test_hidden_player_and_mission_sorties_stay_404(client: Client) -> None:
    save(seed())
    Player.objects.filter(pk=player_pk(1)).update(is_hidden=True)
    assert client.get(f"/sorties/{pk_of(1)}/").status_code == 404
    Player.objects.update(is_hidden=False)
    Mission.objects.update(is_hidden=True)
    assert client.get(f"/sorties/{pk_of(2)}/").status_code == 404


# --- profile: PvE ---
def test_profile_shows_the_pve_breakdown(client: Client) -> None:
    save(seed())

    body = client.get(f"/players/{player_pk(1)}/").content.decode()
    rows = {r.label: r for r in client.get(f"/players/{player_pk(1)}/").context["pve_kills"]}

    assert "Who kills you, and whom you kill" in body
    assert (rows["Player aircraft"].count, rows["AI aircraft"].count) == (1, 2)
    assert (rows["Anti-aircraft"].count, rows["Other ground objects"].count) == (2, 1)


def test_profile_deaths_by_class_follow_the_counters(client: Client) -> None:
    save(seed())

    flak = client.get(f"/players/{player_pk(2)}/").context["pve_losses"]
    crash = client.get(f"/players/{player_pk(3)}/").context["pve_losses"]

    assert {r.key: (r.deaths, r.planes_lost, r.share) for r in flak if r.deaths} == {"aaa": (1, 1, "100%")}
    assert {r.key: r.deaths for r in crash if r.deaths} == {"environment": 1}
    assert "1 death, 100% to anti-aircraft" in client.get(f"/players/{player_pk(2)}/").content.decode()


def test_profile_budget_is_unchanged_by_the_breakdown(client: Client) -> None:
    save(seed())

    assert_simple_reads(
        client, f"/players/{player_pk(1)}/", max_queries=11
    )  # as test_player_pages (tours, stat marks, streak, killboard)


def test_hidden_player_profile_stays_404(client: Client) -> None:
    save(seed())
    Player.objects.filter(pk=player_pk(2)).update(is_hidden=True)

    assert client.get(f"/players/{player_pk(2)}/").status_code == 404


# --- aircraft page ---
def test_aircraft_page_gives_the_average_hits_to_destroy(client: Client) -> None:
    save(seed())

    response = client.get("/aircraft/")

    body = response.content.decode()
    assert response.status_code == 200
    assert "MiG-15bis" in body
    row = next(r for r in response.context["rows"] if r.stats.aircraft.log_name == "MiG-15bis").hits
    assert (row.kills, row.average) == ("2", "4.50")  # 9 hits in 2 kills
    assert [(a.name, a.kills, a.average) for a in row.by_ammo] == [
        (".50 BMG API", "2", "4.00"),
        (".50 BMG INC", "1", "1.00"),
    ]
    assert "Aircraft" in body


def test_aircraft_page_without_data_says_so_and_stays_cheap(client: Client) -> None:
    assert "No aircraft has flown yet." in client.get("/aircraft/").content.decode()

    save(seed())
    assert_simple_reads(client, "/aircraft/", max_queries=2 + 2)


def test_aircraft_page_is_reachable_from_the_navigation(client: Client) -> None:
    assert 'href="/aircraft/"' in client.get("/").content.decode()
