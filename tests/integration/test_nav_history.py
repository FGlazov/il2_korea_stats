"""The header navigation: "History" holds Missions, Sorties and Leaderboards (maintainer requests 2026-10-05, FR-WEB-29,
and 2026-10-28: Leaderboards moved in to leave room for the admin's links).

Players and Aircraft stay top level; the dropdown is a native <details> (no JS needed); the admin's own
links come after it. Synthetic data only."""

import re
from datetime import timedelta

import pytest
from django.test import Client

from il2ks.db.models import Mission, SiteSettings
from tests.factories import STARTED_AT, meta, mission, save, sortie

pytestmark = pytest.mark.django_db


def seed() -> int:
    save(mission((sortie(0, 1, name="Maverick"),)), meta("2026-10-02_10-00-00", STARTED_AT + timedelta(days=13)))
    return Mission.objects.get().pk


def nav_of(client: Client, url: str) -> str:
    html = client.get(url).content.decode()
    return html[html.index('class="site-nav"') : html.index("</nav>")]


def opening_tag(markup: str, pattern: str) -> str:
    found = re.search(pattern, markup)
    assert found is not None, pattern
    return found.group(0)


def test_nav_has_players_aircraft_then_the_history_dropdown_then_the_admin_links(client: Client) -> None:
    SiteSettings.objects.create(pk=1, links=[{"label": "Discord", "url": "https://discord.example/x", "icon": ""}])

    nav = nav_of(client, "/")

    assert nav.index(">Players<") < nav.index(">Aircraft<") < nav.index(">History<")
    assert nav.index(">History<") < nav.index("Discord")
    top_level = re.sub(r"<details.*?</details>", "", nav, flags=re.S)
    assert ">Missions<" not in top_level
    assert ">Sorties<" not in top_level
    assert ">Leaderboards<" not in top_level
    menu = nav[nav.index("<details") : nav.index("</details>")]
    assert re.search(r"<summary[^>]*>History</summary>", menu)
    assert menu.index('href="/missions/"') < menu.index('href="/sorties/"') < menu.index('href="/leaderboards/"')
    assert ">Missions<" in menu
    assert ">Sorties<" in menu
    assert " open" not in opening_tag(menu, r"<details[^>]*>")


@pytest.mark.parametrize(
    ("url", "other"), [("/missions/", "/sorties/"), ("/sorties/", "/missions/"), ("/leaderboards/", "/missions/")]
)
def test_the_dropdown_and_its_link_are_current_on_the_missions_sorties_and_leaderboards_pages(
    client: Client, url: str, other: str
) -> None:
    seed()

    menu = nav_of(client, url)

    assert 'aria-current="page"' in opening_tag(menu, r"<summary[^>]*>")
    assert 'aria-current="page"' in opening_tag(menu, rf'<a href="{url}"[^>]*>')
    assert "aria-current" not in opening_tag(menu, rf'<a href="{other}"[^>]*>')


def test_the_dropdown_is_current_on_a_mission_page_and_not_elsewhere(client: Client) -> None:
    pk = seed()

    assert 'aria-current="page"' in opening_tag(nav_of(client, f"/missions/{pk}/"), r"<summary[^>]*>")
    for url in ("/", "/players/", "/aircraft/"):
        assert "aria-current" not in opening_tag(nav_of(client, url), r"<summary[^>]*>"), url


def test_the_home_page_latest_missions_still_link_to_the_mission_list(client: Client) -> None:
    seed()

    assert 'href="/missions/' in client.get("/").content.decode().split("</header>")[1]
