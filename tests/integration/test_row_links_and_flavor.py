"""Whole-row links (FR-WEB-24) and flavor text (FR-WEB-23) in the rendered pages. Synthetic data only."""

import re

import pytest
from django.test import Client

from il2ks.db.models import Mission, Player, PlayerSortie, Tour
from tests.factories import account, kill, mission, save, sortie

pytestmark = pytest.mark.django_db


def seed() -> Mission:
    return save(
        mission(
            (
                sortie(0, 1, name="Maverick", kills_air=3, kills_air_pvp=3),
                sortie(
                    1, 2, name="Goose", coalition=2, outcome="not_taken_off", taxi_accident=True, is_plane_lost=True
                ),
            ),
            (kill(3000, 0, 1),),
        )
    )


def get(client: Client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def stretched(html: str, target: str) -> bool:
    return re.search(rf'<a class="[^"]*\bstretched-link\b[^"]*" href="{re.escape(target)}"', html) is not None


def test_home_and_mission_list_rows_link_to_the_mission() -> None:
    client = Client()
    saved = seed()
    for url in ("/", "/missions/"):
        assert stretched(get(client, url), f"/missions/{saved.pk}/"), url


def test_home_top_pilots_row_links_to_the_profile_and_shows_a_quip() -> None:
    client = Client()
    seed()
    html = get(client, "/")
    pk = Player.objects.get(account_uuid=account(1)).pk
    assert stretched(html, f"/players/{pk}/?tour={Tour.objects.get().pk}")  # keeps the tour (bare = current)
    assert 'class="flavor"' in html


def test_mission_detail_sortie_rows_link_to_the_sortie_and_the_pilot_name_stays_a_plain_link() -> None:
    client = Client()
    saved = seed()
    html = get(client, f"/missions/{saved.pk}/")
    row = PlayerSortie.objects.get(player__account_uuid=account(1))
    pilot = Player.objects.get(account_uuid=account(1))
    assert stretched(html, f"/sorties/{row.pk}/")
    assert f'<a href="/players/{pilot.pk}/?tour={saved.tour_id}">Maverick</a>' in html  # the mission's tour


def test_player_pages_rows_link_to_their_main_target() -> None:
    client = Client()
    seed()
    pilot = Player.objects.get(account_uuid=account(1))
    row = PlayerSortie.objects.get(player=pilot)
    assert stretched(get(client, "/players/?q=Mav"), f"/players/{pilot.pk}/")
    profile = get(client, f"/players/{pilot.pk}/?tour=all")
    assert stretched(profile, f"/sorties/{row.pk}/")
    assert stretched(profile, f"/players/{pilot.pk}/sorties/?aircraft={row.aircraft_id}&amp;tour=all")
    assert stretched(get(client, f"/players/{pilot.pk}/sorties/"), f"/sorties/{row.pk}/")


def test_sortie_page_has_a_quip_only_for_notable_outcomes() -> None:
    client = Client()
    seed()
    ace = PlayerSortie.objects.get(player__account_uuid=account(1))
    taxi = PlayerSortie.objects.get(player__account_uuid=account(2))
    assert 'class="flavor flavor--sortie"' in get(client, f"/sorties/{ace.pk}/")
    assert 'class="flavor flavor--sortie"' in get(client, f"/sorties/{taxi.pk}/")
    PlayerSortie.objects.filter(pk=ace.pk).update(kills_air=1)
    assert 'class="flavor flavor--sortie"' not in get(client, f"/sorties/{ace.pk}/")


def test_the_quip_is_the_same_on_every_load() -> None:
    client = Client()
    seed()
    pilot = Player.objects.get(account_uuid=account(1))
    first = get(client, f"/players/{pilot.pk}/")
    assert get(client, f"/players/{pilot.pk}/") == first
