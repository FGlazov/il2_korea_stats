"""Page sizes and URL handling of the long lists (OQ-96, NFR-PERF): 10 missions and 20 sorties at a time.

The mission page paginates each side's sortie table and the kills table on their own (`page_redfor`, `page_blufor`,
`page_other`, `page_kills`); sort applies before pagination; links keep every other parameter, every `cols` value
included; hidden players stay anonymous on every page. Synthetic data only."""

import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse

from il2ks.db.models import Mission, Player
from il2ks.queries.paging import MISSION_PAGE_SIZE, ROW_PAGE_SIZE
from tests.factories import account, kill, mission, save, sortie

pytestmark = pytest.mark.django_db

RED = 45
BLUE = 3


def seed() -> Mission:
    """45 REDFOR sorties (R00..R44; R30 is a hidden player; kills_air grows with the index), 3 BLUFOR sorties and 25
    player-versus-player kills."""
    sorties = [sortie(i, i + 1, name=f"R{i:02d}", kills_air=i) for i in range(RED)]
    sorties += [sortie(RED + i, RED + i + 1, name=f"B{i}", coalition=2) for i in range(BLUE)]
    kills = [kill(1000 + i * 10, killer=i, victim=RED + i % BLUE) for i in range(25)]
    result = save(mission(tuple(sorties), tuple(kills)))
    Player.objects.filter(account_uuid=account(31)).update(is_hidden=True)  # R30
    return result


def get(client: Client, pk: int, query: str = "") -> str:
    response = client.get(reverse("web:mission-detail", args=[pk]) + query)
    assert response.status_code == 200
    return response.content.decode()


def names(html: str, prefix: str) -> list[str]:
    """The names in the sortie tables (the kills table, after them, also names pilots)."""
    return re.findall(rf"\b{prefix}\d+\b", html[: html.index('id="kills"')])


def hrefs(html: str, rel: str) -> list[str]:
    return [unescape(m) for m in re.findall(rf'<a href="([^"]*)" hx-get="[^"]*" rel="{rel}"', html)]


def test_page_sizes() -> None:
    assert MISSION_PAGE_SIZE == 10
    assert ROW_PAGE_SIZE == 20


def test_each_side_table_shows_twenty_sorties_with_its_own_page_number(client: Client) -> None:
    pk = seed().pk

    first = get(client, pk)
    third = get(client, pk, "?page_redfor=3")

    assert "Showing 1\N{EN DASH}20 of 45" in first
    assert len(set(names(first, "R"))) == ROW_PAGE_SIZE
    assert "Showing 41\N{EN DASH}45 of 45" in third
    assert set(names(third, "R")) == {"R40", "R41", "R42", "R43", "R44"}
    assert set(names(third, "B")) == {"B0", "B1", "B2"}  # BLUFOR stays on its own page 1
    assert "R44" in get(client, pk, "?page_redfor=99")  # out of range: the last page


def test_sort_applies_before_pagination_and_the_links_keep_sort_and_every_column(client: Client) -> None:
    pk = seed().pk

    html = get(client, pk, "?sort=-kills_air&cols=landings&cols=damage_taken")

    assert names(html, "R")[:3] == ["R44", "R43", "R42"]  # the best of all 45, not of the first 20
    next_href = hrefs(html, "next")[0]
    query = parse_qs(urlsplit(next_href).query)
    assert query["sort"] == ["-kills_air"]
    assert query["cols"] == ["landings", "damage_taken"]  # every value of a repeated parameter survives
    assert query["page_redfor"] == ["2"]
    page_two = get(client, pk, next_href)
    assert names(page_two, "R")[:2] == ["R24", "R23"]
    assert 'aria-sort="descending"' in page_two


def test_paging_one_table_keeps_the_page_of_the_others_and_a_new_sort_starts_over(client: Client) -> None:
    pk = seed().pk

    html = get(client, pk, "?page_redfor=2&page_kills=2")

    kills_prev = [h for h in hrefs(html, "prev") if "page_kills" not in h]
    assert kills_prev  # the kills pager goes back to page 1 by dropping page_kills ...
    assert all("page_redfor=2" in h for h in kills_prev)  # ... and keeps the sortie table where it was
    sort_links = [unescape(h) for h in re.findall(r'<th[^>]*><a href="([^"]*sort=[^"]*)"', html)]
    assert sort_links
    assert all("page_" not in link for link in sort_links)


def test_kills_are_paginated_inside_the_same_region(client: Client) -> None:
    pk = seed().pk

    first = get(client, pk)
    second = get(client, pk, "?page_kills=2")

    assert first.count('class="kill-who"') == ROW_PAGE_SIZE * 2
    assert second.count('class="kill-who"') == 5 * 2
    assert "Showing 21\N{EN DASH}25 of 25" in second
    assert 'id="kills"' in first[first.index('id="results"') :]  # so the kills pager refreshes the region


def test_a_hidden_player_is_anonymous_on_every_page(client: Client) -> None:
    pk = seed().pk

    for query in ("", "?page_redfor=2", "?page_redfor=2&sort=-kills_air", "?page_kills=2"):
        assert "R30" not in get(client, pk, query), query
    assert "Hidden player" in get(client, pk, "?page_redfor=2")
