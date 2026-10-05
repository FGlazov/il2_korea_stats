"""Whole-row links on a table with a sticky first column (code review #13, FR-WEB-24).

The sticky first cell is the containing block of the link's `::after` overlay, so the overlay covers only that cell.
A click anywhere else on the row is a script click: it must still open the row like a link does (plain click: here;
middle-click and ctrl/meta/shift-click: a new tab) without hijacking other links or controls in the row, and the focus
ring must outline the whole row, not just the first cell."""

import re

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e.world import World

pytestmark = pytest.mark.e2e

ROW = ".data-table tbody tr:has(a.stretched-link)"
PLAYER_URL = re.compile(r"/players/\d+/")


def far_cell(page: Page) -> Locator:
    """A cell of the first player row that is neither the first (sticky, covered by the overlay) nor a link."""
    page.goto("/players/?q=")
    return page.locator(ROW).first.locator("td.num").nth(1)


def test_middle_click_on_a_far_cell_opens_the_row_in_a_new_tab(page: Page) -> None:
    cell = far_cell(page)
    with page.context.expect_page() as opened:
        cell.click(button="middle")
    tab = opened.value
    tab.wait_for_url(PLAYER_URL)
    assert PLAYER_URL.search(tab.url), tab.url
    assert "/players/?q=" in page.url  # this tab stayed where it was
    tab.close()


@pytest.mark.parametrize("modifier", ["Control", "Shift"])
def test_modifier_click_on_a_far_cell_opens_the_row_in_a_new_tab(page: Page, modifier: str) -> None:
    cell = far_cell(page)
    with page.context.expect_page() as opened:
        cell.click(modifiers=[modifier])  # type: ignore[list-item]
    tab = opened.value
    tab.wait_for_url(PLAYER_URL)
    assert PLAYER_URL.search(tab.url), tab.url
    assert "/players/?q=" in page.url
    tab.close()


def test_plain_click_on_a_far_cell_opens_the_row_here(page: Page) -> None:
    far_cell(page).click()
    expect(page).to_have_url(PLAYER_URL)


def test_a_click_on_another_link_in_the_row_is_left_alone(page: Page, world: World) -> None:
    """In a mission's sortie table the pilot name is a second link in the row: it goes to the pilot."""
    page.goto(f"/missions/{world.featured_mission_pk}/")
    other = page.locator(f"{ROW} a:not(.stretched-link)").first
    other.click()
    expect(page).to_have_url(PLAYER_URL)


def test_the_focus_ring_outlines_the_whole_row(page: Page) -> None:
    page.goto("/players/?q=")
    link = page.locator(f"{ROW} a.stretched-link").first
    for _ in range(80):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.matches('a.stretched-link')"):
            break
    expect(link).to_be_focused()
    rings = page.evaluate(
        """() => [...document.activeElement.closest('tr').children].map(cell => {
            const style = getComputedStyle(cell);
            return style.boxShadow !== 'none' || (style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0);
        })"""
    )
    assert len(rings) > 2
    assert all(rings), f"cells of the focused row without a ring: {rings}"
