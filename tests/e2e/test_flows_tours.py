"""A pilot who did not fly in a tour (maintainer, 2026-10-05: tours are a clean slate, so a profile is often missing in
the tour a visitor switches to): the page says so, keeps the tour selector, and offers the way back.

Data: `tests/e2e/world.py`. Bob Wingman flew only in the featured mission, in the current tour; the filler missions go
back two more months, where he did not fly. Asserted: the notice, the links and the selector, never flavor text.
"""

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e import flow_helpers
from tests.e2e.flow_helpers import settle, tour_select
from tests.e2e.helpers import main_region
from tests.e2e.world import World

patient_timeouts = flow_helpers.patient_timeouts  # the long flows get generous timeouts
pytestmark = pytest.mark.usefixtures("patient_timeouts")


def test_a_visitor_switches_a_profile_to_a_tour_the_pilot_did_not_fly_in_and_back(page: Page, world: World) -> None:
    """Bob's profile (current tour) -> an older tour: "did not fly in" with All time and his own tour as ways out, the
    selector still there -> "All time" from the notice -> back to the current tour from the selector."""
    page.goto("/players/?q=" + world.bob.replace(" ", "+"))
    page.get_by_role("link", name=world.bob, exact=True).first.click()
    expect(page).to_have_url(re.compile(r"/players/\d+/"))
    profile = re.sub(r"\?.*", "", page.url)
    expect(main_region(page).get_by_text("did not fly in")).to_have_count(0)  # he flew in the current tour

    options = tour_select(page).locator("option")
    older = [
        options.nth(i).inner_text().strip()
        for i in range(options.count())
        if re.search(r"\d{4}", options.nth(i).inner_text())
    ][-1]  # the oldest tour: only the fillers flew there
    tour_select(page).select_option(label=older)
    settle(page)
    notice = main_region(page).get_by_role("note").filter(has_text="did not fly in")
    expect(notice).to_contain_text(f"{world.bob} did not fly in {older}")
    expect(tour_select(page)).to_be_visible()  # the visitor can still pick another tour
    expect(notice.get_by_role("link", name="All time")).to_have_attribute("href", re.compile(r"\?tour=all$"))
    assert notice.get_by_role("link").count() >= 2  # All time and the tour he flew in

    notice.get_by_role("link", name="All time").click()
    expect(page).to_have_url(re.compile(r"[?&]tour=all"))
    expect(main_region(page).get_by_text("did not fly in")).to_have_count(0)
    expect(main_region(page).get_by_role("heading", name=re.compile("Recent sorties|Sorties"))).not_to_have_count(0)

    tour_select(page).select_option(label="Current tour")
    settle(page)
    expect(page).not_to_have_url(re.compile(r"tour=all"))
    expect(main_region(page).get_by_text("did not fly in")).to_have_count(0)
    assert page.url.startswith(profile)
