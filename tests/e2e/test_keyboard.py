"""Keyboard-only use (WCAG 2.1.1): the column picker, the tour dropdown, the leaderboard switcher and the pagination
work with Tab, Space, Enter and the arrow keys, and focus is visible where it lands."""

import re

from playwright.sync_api import Page, expect

FOCUS_RING = """() => { const s = getComputedStyle(document.activeElement);
  return (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) || s.boxShadow !== 'none'; }"""


def tab_to(page: Page, selector: str, limit: int = 80) -> None:
    """Press Tab until the element matching `selector` has focus; fail if it is never reached."""
    for _ in range(limit):
        if page.evaluate("sel => !!document.activeElement && document.activeElement.matches(sel)", selector):
            return
        page.keyboard.press("Tab")
    raise AssertionError(f"Tab never reached {selector}")


def test_column_picker_by_keyboard(page: Page) -> None:
    page.goto("/players/?q=")
    tab_to(page, "details.columns-picker > summary")
    assert page.evaluate(FOCUS_RING), "the Extra columns summary shows no focus"
    page.keyboard.press("Enter")
    expect(page.locator("details.columns-picker")).to_have_attribute("open", "")
    page.keyboard.press("Tab")
    checkbox = page.locator("details.columns-picker input[type=checkbox]").first
    expect(checkbox).to_be_focused()
    page.keyboard.press("Space")
    expect(checkbox).to_be_checked()
    expect(page).to_have_url(re.compile(r"cols="))


def test_tour_dropdown_by_keyboard(page: Page) -> None:
    page.goto("/leaderboards/air/")
    tab_to(page, "select[name=tour]")
    assert page.evaluate(FOCUS_RING), "the tour select shows no focus"
    page.keyboard.press("ArrowDown")  # on a closed select the arrows change the value and fire `change`
    expect(page).to_have_url(re.compile(r"tour="))


def test_board_switcher_by_keyboard(page: Page) -> None:
    page.goto("/leaderboards/")
    tab_to(page, ".board-tabs a")
    assert page.evaluate(FOCUS_RING), "the board switcher shows no focus"
    target = page.evaluate("document.activeElement.getAttribute('href')")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(re.escape(target)))
    expect(page.get_by_role("heading", level=1)).to_be_visible()


def test_pagination_by_keyboard(page: Page) -> None:
    page.goto("/missions/")
    tab_to(page, ".pagination a[rel=next]")
    assert page.evaluate(FOCUS_RING), "the pagination link shows no focus"
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"page=2"))
