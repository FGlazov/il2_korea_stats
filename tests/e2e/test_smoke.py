"""Smoke tests: the pages render, the browser logs no errors, the theme toggle and the htmx lists work (doc 08).

The first group runs against what is on main (home page, style guide, admin login, 404); the rest needs the real pages
and is marked `xfail(strict=False)` until they are merged (see `helpers.PAGES_PENDING`)."""

import re
from collections.abc import Callable

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.helpers import PAGES_PENDING, expect_same_document, header_search, link_or_button, mark_page
from tests.e2e.world import World

type Url = Callable[[World], str]
PUBLIC_PAGES: dict[str, Url] = {
    "home": lambda w: "/",
    "mission list": lambda w: "/missions/",
    "mission": lambda w: f"/missions/{w.featured_mission_pk}/",
    "player search": lambda w: "/players/",
    "player search with a name": lambda w: "/players/?q=Ace",
    "player": lambda w: f"/players/{w.ace_pk}/",
    "player's sorties": lambda w: f"/players/{w.ace_pk}/sorties/",
    "sortie": lambda w: f"/sorties/{w.ace_sortie_pk}/",
    "mission from a real (anonymized) log": lambda w: f"/missions/{w.logs_mission_pk}/",
    "player from a real log": lambda w: f"/players/{w.logs_player_pk}/",
    "player's sorties from a real log": lambda w: f"/players/{w.logs_player_pk}/sorties/",
    "sortie from a real log": lambda w: f"/sorties/{w.logs_sortie_pk}/",
}


def test_home_page(page: Page) -> None:
    page.goto("/")

    expect(page.get_by_role("heading", level=1)).to_be_visible()
    expect(link_or_button(page, "Browse missions")).to_be_visible()
    expect(link_or_button(page, "Find a player")).to_be_visible()
    expect(header_search(page)).to_be_visible()
    expect(page.get_by_role("navigation", name="Main").get_by_role("link", name="Missions")).to_be_visible()
    expect(page.get_by_role("navigation", name="Main").get_by_role("link", name="Players")).to_be_visible()


def test_the_reload_detector_notices_a_full_page_load(page: Page) -> None:
    """Harness self-test: `expect_same_document` (used for the htmx checks) fails after a real navigation."""
    page.goto("/")
    mark_page(page)
    expect_same_document(page)

    page.reload()

    with pytest.raises(AssertionError, match="reloaded"):
        expect_same_document(page)


def test_home_links_lead_to_the_lists(page: Page) -> None:
    page.goto("/")
    link_or_button(page, "Browse missions").click()
    expect(page).to_have_url(re.compile(r"/missions/$"))
    page.goto("/")
    link_or_button(page, "Find a player").click()
    expect(page).to_have_url(re.compile(r"/players/$"))


@pytest.mark.parametrize("name", PUBLIC_PAGES)
def test_every_public_page_renders_without_console_errors(page: Page, world: World, name: str) -> None:
    """Each page answers 200, has its own h1, the shared header and footer, and the console stays clean
    (the autouse `console_errors` fixture fails the test otherwise)."""
    response = page.goto(PUBLIC_PAGES[name](world))

    assert response is not None
    assert response.status == 200
    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    expect(page.get_by_role("banner")).to_be_visible()
    expect(page.get_by_role("contentinfo")).to_be_visible()
    expect(page.get_by_role("main")).to_be_visible()


def test_the_style_guide_is_there_in_debug_mode(page: Page) -> None:
    """`il2ks web --dev` is debug mode, where the style guide is served (it is a 404 otherwise)."""
    response = page.goto("/_styleguide/")

    assert response is not None
    assert response.status == 200
    expect(page.get_by_role("heading", name="Style guide", level=1)).to_be_visible()


def test_the_admin_login_page(page: Page) -> None:
    page.goto("/admin/")

    expect(page).to_have_url(re.compile(r"/admin/login/"))
    expect(page.get_by_label("Username")).to_be_visible()
    expect(page.get_by_label("Password")).to_be_visible()
    expect(page.get_by_role("button", name=re.compile("log in", re.IGNORECASE))).to_be_visible()


@pytest.mark.allow_console_errors  # the browser logs the failed request itself
def test_an_unknown_address_is_a_404(page: Page) -> None:
    """Debug mode (this server) shows Django's technical 404 for an address that matches no route, not our template."""
    response = page.goto("/there-is-no-such-page/")

    assert response is not None
    assert response.status == 404
    expect(page.get_by_role("heading", name="Page not found")).to_be_visible()


@PAGES_PENDING
@pytest.mark.allow_console_errors
def test_an_unknown_mission_shows_our_not_found_page(page: Page) -> None:
    """A missing object (the view raises Http404) is rendered by `404.html` even in debug mode."""
    response = page.goto("/missions/999999/")

    assert response is not None
    assert response.status == 404
    expect(page.get_by_role("heading", name="Page not found")).to_be_visible()
    link_or_button(page, "Back to the start page").click()
    expect(page).to_have_url(re.compile(r"/$"))


def test_dark_mode_toggle_switches_and_is_remembered(page: Page) -> None:
    page.emulate_media(color_scheme="light")
    page.goto("/")
    html = page.locator("html")
    toggle = page.get_by_role("button", name="Switch between light and dark")

    toggle.click()
    expect(html).to_have_attribute("data-theme", "dark")
    page.reload()
    expect(html).to_have_attribute("data-theme", "dark")  # remembered (localStorage)

    toggle.click()
    expect(html).to_have_attribute("data-theme", "light")


def test_the_theme_can_be_forced_in_the_address(page: Page) -> None:
    page.goto("/?theme=dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


# --- needs the real list pages --------------------------------------------------------------------------------------


@PAGES_PENDING
def test_mission_list_paginates_in_place_and_updates_the_url(page: Page, world: World) -> None:
    """FR-WEB-2 + the htmx list pattern: "Next" swaps the table, keeps the document, pushes `?page=2`."""
    page.goto("/missions/")
    mark_page(page)

    page.get_by_role("link", name="Next").click()

    expect(page).to_have_url(re.compile(r"[?&]page=2"))
    expect(page.get_by_role("navigation", name="Pagination")).to_contain_text("Showing")
    expect_same_document(page)
    page.go_back()  # the pushed URL is a real history entry
    expect(page).not_to_have_url(re.compile(r"page=2"))


@PAGES_PENDING
def test_mission_list_sorts_in_place_and_updates_the_url(page: Page) -> None:
    """Clicking a column header sorts the table through htmx and pushes `?sort=...` (sort_th component)."""
    page.goto("/missions/")
    mark_page(page)

    header = page.get_by_role("columnheader").nth(1)
    header.get_by_role("link").click()

    expect(page).to_have_url(re.compile(r"[?&]sort=-?\w+"))
    expect(header).to_have_attribute("aria-sort", re.compile("ascending|descending"))
    expect_same_document(page)
