"""The pilot fate on the pages (maintainer request 2026-10-04): Dead / Captured / Survived instead of the stored fate,
next to the outcome in the sortie lists, and no default Mission column there. Synthetic data only."""

import re

import pytest
from django.test import Client
from django.urls import reverse

from il2ks.db.models import Mission, Player, PlayerSortie
from tests.factories import mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db


def seed() -> None:
    """Player 1: dead after a bailout, captured, a survivor who bailed out, and one with an unknown fate."""
    save(
        mission(
            (
                sortie(0, 1, outcome="shot_down", pilot_fate="bailed_out", is_death=True, is_plane_lost=True),
                sortie(1, 1, outcome="crashed", pilot_fate="bailed_out", is_plane_lost=True),
                sortie(2, 1, outcome="landed", pilot_fate="bailed_out"),
                sortie(3, 1, outcome="landed", pilot_fate="unknown"),
            )
        )
    )
    PlayerSortie.objects.filter(spawn_tick=2000).update(is_captured=True, pilot_status="captured")


def badge_texts(html: str) -> list[str]:
    return re.findall(
        r'<span class="badge badge--(?:red|orange|green)[^"]*"[^>]*>(?:<svg.*?</svg>)?([^<]+)</span>', html, re.S
    )


def headers(html: str) -> list[str]:
    head = html.split("<thead>", 1)[1].split("</thead>", 1)[0]
    return [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<th\b.*?</th>", head, re.S)]


def test_sortie_list_shows_the_fate_right_after_the_outcome_and_no_default_mission_column(client: Client) -> None:
    seed()
    url = reverse("web:player-sorties", args=[Player.objects.get().pk]) + "?tour=all"
    html = client.get(url).content.decode()

    cols = headers(html)
    assert cols[cols.index("Outcome") + 1] == "Pilot fate"
    assert "Mission" not in cols
    assert badge_texts(html).count("Dead") == 1
    assert badge_texts(html).count("Captured") == 1
    assert badge_texts(html).count("Survived") == 2  # the bailout survivor and the unknown fate
    assert 'title="Bailed out"' in html  # the detailed fate stays as a tooltip


def test_mission_is_an_optional_column_of_the_sortie_list(client: Client) -> None:
    seed()
    url = reverse("web:player-sorties", args=[Player.objects.get().pk]) + "?tour=all"
    chosen = client.get(url + "&cols=mission").content.decode()

    assert "Mission" in headers(chosen)
    assert re.search(r'<a href="/missions/\d+/">', chosen)
    assert re.search(r"sort=-?mission", chosen)
    assert 'value="mission"' in chosen  # offered in the columns picker


def test_sortie_list_budget_is_unchanged(client: Client) -> None:
    seed()
    base = reverse("web:player-sorties", args=[Player.objects.get().pk]) + "?tour=all"
    assert_simple_reads(client, base, max_queries=8)
    assert_simple_reads(client, base + "&cols=mission", max_queries=8)


def test_profile_recent_block_has_the_fate_and_no_mission_column(client: Client) -> None:
    seed()
    player = Player.objects.get()
    response = client.get(reverse("web:player-detail", args=[player.pk]) + "?tour=all")
    html = response.content.decode()
    block = html.split('id="recent"', 1)[1].split("</section>", 1)[0]

    cols = headers(block)
    assert cols[cols.index("Outcome") + 1] == "Pilot fate"
    assert "Mission" not in cols
    assert badge_texts(block).count("Dead") == 1
    assert badge_texts(block).count("Captured") == 1
    assert badge_texts(block).count("Survived") == 2
    assert f"/missions/{Mission.objects.get().pk}/" not in block  # whole-row links go to the sortie only


def test_mission_page_shows_the_displayed_fate(client: Client) -> None:
    seed()
    html = client.get(reverse("web:mission-detail", args=[Mission.objects.get().pk])).content.decode()
    texts = badge_texts(html)
    assert [texts.count("Dead"), texts.count("Captured"), texts.count("Survived")] == [1, 1, 2]


def test_sortie_page_shows_the_fate_with_its_detail_and_the_timeline_end(client: Client) -> None:
    seed()

    def page(tick: int) -> str:
        pk = PlayerSortie.objects.get(spawn_tick=tick).pk
        return client.get(f"/sorties/{pk}/").content.decode()

    dead = page(1000)
    assert '>Dead</span> <small class="muted">(bailed out)</small>' in dead
    assert "Pilot status" not in dead  # the status adds nothing once the pilot is dead

    captured = page(2000)
    assert '>Captured</span> <small class="muted">(bailed out)</small>' in captured
    assert "Pilot status" not in captured

    survivor = page(3000)
    assert '>Survived</span> <small class="muted">(bailed out)</small>' in survivor
    assert "Pilot status" in survivor

    unknown = page(4000)
    assert ">Survived</span>" in unknown
    assert "(bailed out)" not in unknown
