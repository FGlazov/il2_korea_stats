"""The optional "Columns" control on every list that has it: several ticked columns all show up (maintainer bug
2026-10-04: after the first tick the htmx swap detached the listener from the preserved checkboxes, so only the first
column could be added). With htmx (each tick refreshes in place) and without JS (Apply submits repeated `cols=`)."""

import re

import pytest
from playwright.sync_api import Browser, Locator, Page, expect

from tests.e2e.helpers import expect_same_document, mark_page
from tests.e2e.world import World

pytestmark = pytest.mark.e2e


def list_pages(world: World) -> list[tuple[str, list[str]]]:
    """(path, three optional columns) of every page that has the control."""
    return [
        ("/players/?q=", ["K/D", "Survival", "Planes lost"]),
        ("/missions/", ["Friendly kills", "Ended", "Sorties per player"]),
        ("/aircraft/", ["Bailouts", "Assists", "Sortie length"]),
        (f"/players/{world.ace_pk}/sorties/", ["Takeoffs", "Landings", "Loadout"]),
        (f"/missions/{world.featured_mission_pk}/", ["Damage taken", "Takeoffs", "Landings"]),
    ]


def header(page: Page, label: str) -> Locator:
    return page.locator("thead th").filter(has_text=re.compile(f"^\\s*{re.escape(label)}\\s*$", re.I)).first


@pytest.mark.parametrize("index", range(5))
def test_ticking_three_columns_with_htmx(page: Page, world: World, index: int) -> None:
    path, labels = list_pages(world)[index]
    page.goto(path)
    mark_page(page)
    page.get_by_text("Columns", exact=True).click()
    for label in labels:
        page.get_by_role("checkbox", name=label, exact=True).check()
        expect(header(page, label)).to_be_visible()
    for label in labels:
        expect(header(page, label)).to_be_visible()
    expect_same_document(page)  # updated in place
    assert page.url.count("cols=") == 3, page.url


@pytest.mark.parametrize("index", range(5))
def test_ticking_three_columns_without_js(browser: Browser, base_url: str, world: World, index: int) -> None:
    path, labels = list_pages(world)[index]
    context = browser.new_context(java_script_enabled=False, base_url=base_url)
    page = context.new_page()
    try:
        page.goto(path)
        page.locator("details.columns-picker summary").click()
        for label in labels:
            page.get_by_role("checkbox", name=label, exact=True).check()
        page.locator("details.columns-picker summary").click()  # without JS nothing closes the dropdown but its summary
        page.get_by_role("button", name="Apply").first.click()
        page.wait_for_load_state()
        for label in labels:
            expect(header(page, label)).to_be_visible()
    finally:
        context.close()
