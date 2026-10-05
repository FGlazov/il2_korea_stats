"""The player profile's role toggle and the condensed "Other totals" grid (maintainer, 2026-10-05).

Data: `tests/e2e/world.py` (Ace flies air superiority). Asserted: the toggle switches the scope and keeps the tour, the
air-to-ground part comes first in the attack view, and "Other totals" is a 4-column grid on a desktop, 2 columns on a
phone, with no horizontal page scroll at 360 px.
"""

import re

from playwright.sync_api import Page, expect

from tests.e2e.flow_helpers import no_horizontal_scroll
from tests.e2e.helpers import main_region
from tests.e2e.world import World

GRID_COLUMNS = "getComputedStyle(document.querySelector('.totals-grid')).gridTemplateColumns.split(' ').length"


def test_the_role_toggle_switches_the_profile_scope_and_reorders_the_parts(page: Page, world: World) -> None:
    page.goto(f"/players/{world.ace_pk}/?tour=all")
    toggle = main_region(page).get_by_role("group", name="Which sorties to count")
    expect(toggle).to_be_visible()
    air_part, ground_part = page.locator("#air"), page.locator("#ground")
    assert air_part.bounding_box()["y"] < ground_part.bounding_box()["y"]  # type: ignore[index]

    toggle.get_by_role("link", name="Attack").click()

    expect(page).to_have_url(re.compile(r"[?&]role=attack"))
    expect(page).to_have_url(re.compile(r"[?&]tour=all"))  # the tour stays
    expect(toggle.get_by_role("link", name="Attack")).to_have_attribute("aria-current", "true")
    # Ace flew no attack sortie: a notice instead of the parts, the toggle stays to go back
    expect(main_region(page).get_by_text("No sorties in this role").first).to_be_visible()

    toggle.get_by_role("link", name="Air superiority").click()

    expect(page).to_have_url(re.compile(r"[?&]role=air_superiority"))
    expect(page.locator("#air")).to_be_visible()
    expect(page.locator("#ground")).to_be_visible()


def test_other_totals_are_four_columns_on_a_desktop_and_two_on_a_phone(page: Page, world: World) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"/players/{world.ace_pk}/?tour=all")
    assert page.evaluate(GRID_COLUMNS) == 4

    for role in ("", "&role=air_superiority"):
        page.set_viewport_size({"width": 360, "height": 800})
        page.goto(f"/players/{world.ace_pk}/?tour=all{role}")
        assert page.evaluate(GRID_COLUMNS) == 2
        assert no_horizontal_scroll(page)
