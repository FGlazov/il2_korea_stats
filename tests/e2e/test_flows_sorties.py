"""The site-wide sortie list (maintainer request 2026-10-05, FR-WEB-29): reached from the "History" dropdown in the
header, filtered by pilot, then a row opens the sortie report. The dropdown works by keyboard (Escape closes it and
returns the focus to its summary). Data: `tests/e2e/world.py`."""

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e import flow_helpers
from tests.e2e.flow_helpers import name_of, names_in, rows_of, settle, tab_to, table_with
from tests.e2e.helpers import expect_heading, main_region
from tests.e2e.world import World

patient_timeouts = flow_helpers.patient_timeouts
pytestmark = pytest.mark.usefixtures("patient_timeouts")


def history_menu(page: Page):  # noqa: ANN201 - a Locator
    return page.get_by_role("navigation", name="Main").locator("details.dropdown")


def test_someone_opens_the_history_menu_goes_to_sorties_filters_by_pilot_and_opens_a_sortie(
    page: Page, world: World
) -> None:
    page.goto("/")
    menu = history_menu(page)
    menu.locator("summary").click()
    expect(menu).to_have_attribute("open", "")
    menu.get_by_role("link", name="Sorties", exact=True).click()

    expect(page).to_have_url(re.compile(r"/sorties/$"))
    expect_heading(page, re.compile(r"^Sorties"), level=1)
    expect(menu.locator("summary")).to_have_attribute("aria-current", "page")
    table = table_with(page, "Date", "Pilot", "Aircraft", "Mission", "Outcome")
    assert len(rows_of(table)) > 1

    search = main_region(page).get_by_role("searchbox", name="Pilot")
    search.fill(world.ace)
    search.press("Enter")
    expect(page).to_have_url(re.compile(r"[?&]q=Ace"))
    settle(page)
    pilots = {name_of(name) for name in names_in(rows_of(table_with(page, "Date", "Pilot")), "Pilot")}
    assert pilots
    assert pilots <= {world.ace}

    first = main_region(page).locator("tbody tr").first
    first.locator("a.stretched-link").click()
    expect(page).to_have_url(re.compile(r"/sorties/\d+/$"))
    expect_heading(page, re.compile("."), level=1)


def test_the_history_menu_by_keyboard(page: Page) -> None:
    page.goto("/")
    menu = history_menu(page)
    tab_to(page, "nav details.dropdown > summary")
    page.keyboard.press("Enter")
    expect(menu).to_have_attribute("open", "")
    page.keyboard.press("Tab")
    expect(menu.get_by_role("link", name="Missions", exact=True)).to_be_focused()
    page.keyboard.press("Escape")
    expect(menu).not_to_have_attribute("open", "")
    expect(menu.locator("summary")).to_be_focused()


def test_the_history_menu_on_a_phone(page: Page) -> None:
    page.set_viewport_size({"width": 360, "height": 800})
    page.goto("/missions/")
    menu = history_menu(page)
    menu.locator("summary").click()
    sorties = menu.get_by_role("link", name="Sorties", exact=True)
    expect(sorties).to_be_visible()
    box = sorties.bounding_box()
    assert box is not None
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= 360
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    sorties.click()
    expect(page).to_have_url(re.compile(r"/sorties/$"))
