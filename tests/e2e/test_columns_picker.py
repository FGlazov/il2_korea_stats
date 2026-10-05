"""The optional "Extra columns" control on every list that has it: several ticked columns all show up (maintainer bug
2026-10-04: after the first tick the htmx swap detached the listener from the preserved checkboxes, so only the first
column could be added). With htmx (each tick refreshes in place) and without JS (Apply submits repeated `cols=`)."""

import re

import pytest
from playwright.sync_api import Browser, Locator, Page, expect

from tests.e2e.helpers import expect_same_document, mark_page
from tests.e2e.test_accessibility import SCHEMES, WIDTHS, axe_violations
from tests.e2e.test_visual_qa import with_theme
from tests.e2e.world import World

pytestmark = pytest.mark.e2e


def list_pages(world: World) -> list[tuple[str, list[str]]]:
    """(path, three optional columns) of every page that has the control."""
    return [
        ("/players/?q=", ["K/D", "Survival", "Aircraft lost"]),
        ("/missions/", ["Friendly kills", "Ended", "Sorties per pilot"]),
        ("/aircraft/", ["Bailouts", "Assists", "Sortie length"]),
        (f"/players/{world.ace_pk}/sorties/", ["Takeoffs", "Landings", "Loadout"]),
        (f"/missions/{world.featured_mission_pk}/", ["Damage taken", "Takeoffs", "Landings"]),
    ]


def header(page: Page, label: str) -> Locator:
    """The column header called `label` (by its accessible name: a description inside the header is not part of it)."""
    name = re.compile(f"^\\s*{re.escape(label).replace('/', '\\/')}(\\s*[↕▲▼])?\\s*$", re.I)
    return page.get_by_role("columnheader", name=name).first


@pytest.mark.parametrize("index", range(5))
def test_ticking_three_columns_with_htmx(page: Page, world: World, index: int) -> None:
    path, labels = list_pages(world)[index]
    page.goto(path)
    mark_page(page)
    page.get_by_text("Extra columns", exact=True).click()
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
        expect(page.locator("details.columns-picker")).to_have_attribute("open", "")  # ticked columns: starts open
    finally:
        context.close()


# --- Extra columns: the collapsible, the horizontal scroll and the sticky first column (maintainer, 2026-10-05) ---


def first_cell_box(page: Page) -> dict[str, float]:
    """Left and right edge of the first body cell and of the scrolling table wrapper, in viewport pixels."""
    return page.evaluate(
        """() => {
            const wrap = document.querySelector('#results .table-wrap');
            const cell = wrap.querySelector('tbody tr > :first-child');
            const box = cell.getBoundingClientRect(), outer = wrap.getBoundingClientRect();
            return {left: box.left, right: box.right, wrapLeft: outer.left, wrapRight: outer.right};
        }"""
    )


def scroll_left(page: Page) -> float:
    return page.evaluate("document.querySelector('#results .table-wrap').scrollLeft")


def test_extra_columns_is_a_collapsible_with_every_choice_on_the_page(page: Page, world: World) -> None:
    """Closed until used; opening shows every checkbox at once (no inner scrolling) in a multi-column grid."""
    page.goto("/players/?q=")
    picker = page.locator("details.columns-picker")
    expect(picker).not_to_have_attribute("open", "")
    expect(page.get_by_role("checkbox", name="K/D", exact=True)).to_be_hidden()
    page.get_by_text("Extra columns", exact=True).click()
    boxes = picker.get_by_role("checkbox")
    count = boxes.count()
    assert count >= 8, count
    for index in range(count):
        expect(boxes.nth(index)).to_be_visible()
    tops = {round(boxes.nth(index).bounding_box()["y"]) for index in range(count)}  # type: ignore[index]
    assert len(tops) < count, "the choices are laid out in one column"
    assert picker.evaluate("el => el.scrollHeight <= el.clientHeight + 1")


def test_ticking_a_column_keeps_the_scroll_the_focus_and_the_open_collapsible(page: Page, world: World) -> None:
    page.set_viewport_size({"width": 700, "height": 900})
    page.goto("/players/?q=")
    mark_page(page)
    page.get_by_text("Extra columns", exact=True).click()
    for label in ("K/D", "Survival", "Aircraft lost"):
        page.get_by_role("checkbox", name=label, exact=True).check()
        expect(header(page, label)).to_be_visible()
    wrap = page.locator("#results .table-wrap")
    assert wrap.evaluate("el => el.scrollWidth > el.clientWidth + 50"), "the table must scroll sideways for this test"
    page.evaluate("document.querySelector('#results .table-wrap').scrollLeft = 120")
    before = scroll_left(page)
    assert before > 0

    page.get_by_role("checkbox", name="Assists", exact=True).check()
    expect(header(page, "Assists")).to_be_attached()
    expect_same_document(page)
    assert abs(scroll_left(page) - before) <= 1, (before, scroll_left(page))
    expect(page.get_by_role("checkbox", name="Assists", exact=True)).to_be_focused()
    expect(page.locator("details.columns-picker")).to_have_attribute("open", "")


def test_the_first_column_stays_visible_after_scrolling_right(page: Page, world: World) -> None:
    page.set_viewport_size({"width": 700, "height": 900})
    page.goto("/players/?q=&cols=kd&cols=survival&cols=planes_lost&cols=assists&cols=elo_jet&cols=elo_prop")
    wrap = page.locator("#results .table-wrap")
    assert wrap.evaluate("el => el.scrollWidth > el.clientWidth + 100")
    before = first_cell_box(page)
    page.evaluate("const w = document.querySelector('#results .table-wrap'); w.scrollLeft = w.scrollWidth")
    after = first_cell_box(page)
    assert scroll_left(page) > 50
    assert abs(after["left"] - before["left"]) <= 1, (before, after)  # still where it was: pinned to the left edge
    assert after["right"] > after["wrapLeft"] + 20
    assert after["left"] >= after["wrapLeft"] - 1
    expect(page.locator("#results tbody tr > :first-child").first).to_be_visible()
    # it is opaque: the element at the pinned cell's centre is the pinned cell itself, not a cell scrolled under it
    row = page.locator("#results tbody tr").first.bounding_box()
    assert row is not None
    covered = page.evaluate(
        "([x, y]) => document.elementFromPoint(x, y).closest('td, th').matches('tbody tr > :first-child')",
        [(after["left"] + after["right"]) / 2, row["y"] + row["height"] / 2],
    )
    assert covered


def test_extra_columns_pass_axe_open_with_columns_ticked(page: Page, world: World) -> None:
    found: list[str] = []
    for scheme in SCHEMES:
        page.emulate_media(color_scheme=scheme)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(with_theme("/players/?q=&cols=kd&cols=survival", scheme))
            page.wait_for_load_state("networkidle")
            expect(page.locator("details.columns-picker")).to_have_attribute("open", "")
            found += axe_violations(page, f"extra columns {width}px {scheme}")
    assert not found, "\n".join(sorted(set(found)))
