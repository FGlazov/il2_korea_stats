"""Column descriptions (maintainer request 2026-10-04): hovering, focusing or tapping a header whose meaning is not
obvious shows its description, fully inside the viewport (the scrolling table wrapper must not clip it), at desktop and
phone width, in both themes, without a horizontal page scrollbar."""

import re

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e.world import World

pytestmark = pytest.mark.e2e

WIDTHS = [1280, 360]


def tip_of(header: Locator) -> Locator:
    return header.get_by_role("tooltip")


def expect_inside_viewport(page: Page, tip: Locator) -> None:
    box = tip.bounding_box()
    assert box is not None
    size = page.viewport_size
    assert size is not None
    assert box["x"] >= 0, box
    assert box["x"] + box["width"] <= size["width"], (box, size)
    assert box["y"] >= 0, box
    assert box["y"] + box["height"] <= size["height"], (box, size)
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 0, "the description made the page scroll sideways"


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_hover_shows_the_description_of_a_sortable_header(page: Page, width: int, scheme: str) -> None:
    page.set_viewport_size({"width": width, "height": 800})
    page.emulate_media(color_scheme="light" if scheme == "light" else "dark")
    page.goto("/leaderboards/ground-hour/")
    header = page.get_by_role("columnheader", name="Time on target")
    tip = tip_of(header)
    expect(tip).to_be_hidden()

    header.hover()

    expect(tip).to_be_visible()
    expect(tip).to_contain_text("attacking enemy ground targets")
    expect_inside_viewport(page, tip)


@pytest.mark.parametrize("width", WIDTHS)
def test_keyboard_focus_shows_the_description_and_escape_hides_it(page: Page, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 800})
    page.goto("/leaderboards/ground-hour/")
    link = page.get_by_role("link", name="Time on target")
    link.focus()

    tip = tip_of(page.get_by_role("columnheader", name="Time on target"))
    expect(tip).to_be_visible()
    expect_inside_viewport(page, tip)
    page.keyboard.press("Escape")
    expect(tip).to_be_hidden()


def test_sort_link_is_described_for_screen_readers(page: Page) -> None:
    page.goto("/leaderboards/ground-hour/")
    link = page.get_by_role("link", name="Time on target")

    expect(link).not_to_have_accessible_name(re.compile("attacking"))  # the description is not part of the name
    description = link.get_attribute("aria-describedby")
    assert description
    expect(page.locator(f"[id='{description}']")).to_contain_text("attacking enemy ground targets")


@pytest.mark.parametrize("width", WIDTHS)
def test_a_plain_header_shows_its_description_on_focus_and_tap(page: Page, world: World, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"/missions/{world.featured_mission_pk}/")
    header = page.get_by_role("columnheader", name="Credit")
    tip = tip_of(header)
    expect(tip).to_be_hidden()

    header.get_by_text("Credit", exact=True).focus()
    expect(tip).to_be_visible()
    expect_inside_viewport(page, tip)

    page.keyboard.press("Escape")
    page.mouse.move(0, 0)
    expect(tip).to_be_hidden()

    header.locator(".col-hint__marker").click()  # what a tap does
    expect(tip).to_be_visible()
    expect_inside_viewport(page, tip)
    page.get_by_role("heading", level=1).click()
    expect(tip).to_be_hidden()
