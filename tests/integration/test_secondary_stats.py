"""The plain figures of the aircraft and mission pages (doc 16): the hall of shame and the vulched pilots counted per
aircraft and mission, no highlight, and every optional list column has a place on the detail page of its row."""

import re
from collections.abc import Iterable
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from il2ks.db.models import AircraftStats, GameObject, Mission, Player, PlayerSortie
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.web.columns import AIRCRAFT_COLUMNS, MISSION_COLUMNS, PLAYER_COLUMNS, SITE_SORTIE_COLUMNS, Column
from tests.factories import kill, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

AIR = "air_superiority"


def incidents() -> Mission:
    """Six MiG sorties: a taxi accident, two botched landings, one strafed, one friendly-fire pilot (one aircraft, two
    ground objects) and one clean landing; a Sabre sortie is clean, a gunner's botched landing counts for nobody."""
    return save(
        mission(
            (
                sortie(0, 1, taxi_accident=True, outcome="not_taken_off", is_plane_lost=True),
                sortie(1, 2, botched_landing=True, outcome="crashed", is_plane_lost=True),
                sortie(2, 3, botched_landing=True),
                sortie(3, 4, strafed_on_ground=True, outcome="shot_down", is_plane_lost=True),
                sortie(4, 5, friendly_kills_air=1, friendly_kills_ground=2),
                sortie(5, 6),
                sortie(6, 7, aircraft_type="F-86A-5", coalition=2),
                sortie(7, 8, aircraft_type="Turret_IL10", role="gunner", botched_landing=True),
            ),
            (kill(20_000, killer=4, victim=5, is_friendly=True, victim_type="MiG-15bis"),),
        )
    )


def section(html: str, title: str) -> dict[str, str]:
    """The label/value pairs under a plain title, hints left out."""
    start = html.index(f'<h2 class="stat-columns__title">{title}</h2>')
    block = html[start : html.index("</section>", start)]
    pairs = re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", block, re.DOTALL)
    return {strip_tags(re.sub(r"<small>.*?</small>", "", dt)).strip(): strip_tags(dd).strip() for dt, dd in pairs}


def aircraft_url(log_name: str) -> str:
    return reverse("web:aircraft-detail", args=[GameObject.objects.get(log_name=log_name).pk])


def get(client: Client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def test_the_mission_counts_its_incidents_from_the_pilot_sorties() -> None:
    saved = incidents()
    saved.refresh_from_db()
    assert saved.taxi_accidents == 1
    assert saved.botched_landings == 2  # not the gunner's
    assert saved.strafed_on_ground == 1


def test_the_aircraft_counts_its_incidents() -> None:
    incidents()
    stats = AircraftStats.objects.get(aircraft__log_name="MiG-15bis")
    assert (stats.taxi_accidents, stats.botched_landings, stats.strafed_on_ground) == (1, 2, 1)
    assert AircraftStats.objects.get(aircraft__log_name="F-86A-5").botched_landings == 0


def test_a_rebuild_gives_the_same_botched_landings() -> None:
    incidents()
    Player.objects.update(botched_landings=99)
    AircraftStats.objects.update(botched_landings=99)
    rebuild_aggregates()
    assert sorted(Player.objects.values_list("botched_landings", flat=True)) == [0, 0, 0, 0, 0, 0, 1, 1]
    assert AircraftStats.objects.get(aircraft__log_name="MiG-15bis").botched_landings == 2


def test_the_mission_page_shows_the_incidents_plainly_and_vulching_outside_the_shame(client: Client) -> None:
    saved = incidents()
    body = get(client, f"/missions/{saved.pk}/")
    assert section(body, "Hall of shame") == {
        "Taxi/takeoff accidents": "1 14.29%",  # of the 7 pilot sorties
        "Botched landings": "2 28.57%",
        "Friendly-fire air kills": "1",
        "Friendly-fire ground kills": "2",
    }
    other = section(body, "Other totals")
    assert other["Strafed on the ground"] == "1"
    assert "Tour" in other
    assert body.count("Botched landings") == 1  # no tile: only the plain list


def test_the_shares_follow_the_language(client: Client) -> None:
    saved = incidents()
    client.cookies["django_language"] = "de"
    assert "16,67\u00a0%" in get(client, aircraft_url("MiG-15bis"))
    assert "14,29\u00a0%" in get(client, f"/missions/{saved.pk}/")


@pytest.mark.parametrize("url", ["/missions/", "/aircraft/", "/players/"])
def test_the_lists_offer_the_incidents_as_extra_columns(client: Client, url: str) -> None:
    incidents()
    keys = ("taxi_accidents", "botched_landings", "strafed_on_ground")
    body = get(client, f"{url}?cols={','.join(keys)}&sort=-botched_landings")
    for label in ("Taxi/takeoff accidents", "Botched landings", "Strafed on the ground"):
        assert re.search(rf"<th[^>]*>.*?{label}", body, re.DOTALL), label


def test_a_running_mission_shows_no_end_time(client: Client) -> None:
    saved = incidents()
    assert "Ended" in section(get(client, f"/missions/{saved.pk}/"), "Other totals")
    Mission.objects.filter(pk=saved.pk).update(is_live=True)
    assert "Ended" not in section(get(client, f"/missions/{saved.pk}/"), "Other totals")


def test_the_aircraft_page_shows_ratios_shame_and_totals_plainly(client: Client) -> None:
    incidents()
    body = get(client, aircraft_url("MiG-15bis"))
    assert list(section(body, "Ratios"))[:2] == ["K/D", "K/L"]
    assert section(body, "Hall of shame") == {
        "Taxi/takeoff accidents": "1 16.67%",
        "Botched landings": "2 33.33%",
        "Friendly-fire air kills": "1",
        "Friendly-fire ground kills": "2",
    }
    other = section(body, "Other totals")
    assert other["Strafed on the ground"] == "1"
    assert other["Sorties per pilot"] == "1.0"
    assert body.index("stat-columns") < body.index("<h2>Matchups")


def test_the_new_figures_cost_no_extra_query(client: Client) -> None:
    saved = incidents()
    assert_simple_reads(client, f"/missions/{saved.pk}/", max_queries=12)


# A column whose detail page says it in other words; None: it is in the sub-line of a tile or the page head.
PLAYER_LABELS: dict[str, str | None] = {
    "survival": "Survival rate",
    "elo_jet": "Elo",
    "elo_prop": "Elo",
    "assists_air": None,
    "assists_ground": None,
    "last_seen": None,
    "first_seen": None,
}
AIRCRAFT_LABELS: dict[str, str | None] = {
    "attack_share": "Attack sorties",
    "kills_per_hour": "Air kills per flight hour",
    "accuracy_air": None,
    "accuracy_ground": None,
}
MISSION_LABELS: dict[str, str | None] = {
    "redfor_sorties": None,
    "blufor_sorties": None,
    "redfor_players": None,
    "blufor_players": None,
}


SORTIE_LABELS: dict[str, str | None] = {
    "combat_role": None,
    "damage_taken": "Aircraft damage",
    "payload": "Payload",
    "takeoffs": "Takeoffs / landings",
    "landings": "Takeoffs / landings",
}


def labels(columns: Iterable[Column[Any]], renamed: dict[str, str | None]) -> list[str]:
    return [text for c in columns if (text := renamed.get(c.key, str(c.label))) is not None]


@pytest.mark.parametrize("label", labels(MISSION_COLUMNS, MISSION_LABELS))
def test_every_optional_mission_column_is_on_the_mission_page(client: Client, label: str) -> None:
    assert label in get(client, f"/missions/{incidents().pk}/")


@pytest.mark.parametrize("label", labels(AIRCRAFT_COLUMNS, AIRCRAFT_LABELS))
def test_every_optional_aircraft_column_is_on_the_aircraft_page(client: Client, label: str) -> None:
    incidents()
    assert label in get(client, aircraft_url("MiG-15bis"))


@pytest.mark.parametrize("label", labels(PLAYER_COLUMNS, PLAYER_LABELS))
def test_every_optional_player_column_is_on_the_profile(client: Client, label: str) -> None:
    fighter = sortie(0, 1, kills_air=2, assists=1, gun_hits_air=5, combat_role=AIR)
    attacker = sortie(1, 1, kills_ground=2, assists_ground=1, gun_hits_ground=5, combat_role="attack")
    save(mission((fighter, attacker)))
    assert label in get(client, f"/players/{Player.objects.get().pk}/?tour=all")


@pytest.mark.parametrize("label", labels(SITE_SORTIE_COLUMNS, SORTIE_LABELS))
def test_every_optional_sortie_column_is_on_the_sortie_page(client: Client, label: str) -> None:
    """Maintainer 2026-11-02: "ground and air score are missing from the sortie detail page"."""
    save(mission((sortie(0, 1, kills_ground=2, combat_role="attack", time_on_target_s=120),)))
    assert label in get(client, f"/sorties/{PlayerSortie.objects.get().pk}/")


def test_a_sortie_lists_its_scores_in_the_plain_figures(client: Client) -> None:
    save(mission((sortie(0, 1, kills_air=2, combat_role=AIR),)))
    PlayerSortie.objects.update(air_points=12, ground_points=-3)
    body = get(client, f"/sorties/{PlayerSortie.objects.get().pk}/")

    pairs = dict(re.findall(r"<dt>([^<]+)</dt>\s*<dd>(.*?)</dd>", body))
    assert pairs["Air score"] == "12"
    assert pairs["Ground score"] == "-3"
    assert body.index("Aircraft status") < body.index("Air score")  # under the summary


def test_the_friendly_fire_kills_are_split_in_air_and_ground_everywhere(client: Client) -> None:
    saved = incidents()
    row = PlayerSortie.objects.get(friendly_kills_air=1)
    pages = {
        "mission": f"/missions/{saved.pk}/",
        "aircraft": aircraft_url("MiG-15bis"),
        "player": f"/players/{row.player_id}/?tour=all",
    }
    for name, url in pages.items():
        body = get(client, url)
        pairs = dict(
            re.findall(r"<dt>(Friendly-fire (?:air|ground) kills)(?: <small>.*?</small>)?</dt>\s*<dd>(.*?)</dd>", body)
        )
        assert pairs == {"Friendly-fire air kills": "1", "Friendly-fire ground kills": "2"}, name
        assert "Friendly-fire kills" not in body, name
    body = get(client, f"/sorties/{row.pk}/")
    assert "1 friendly aircraft and 2 friendly ground targets destroyed in this sortie" in body
    assert "1 air kills, 2 ground kills" in body
