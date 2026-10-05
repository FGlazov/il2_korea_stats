"""Flows of the features that merged late on 2026-10-04: an admin changes the quips and the achievements and a visitor
sees it (FR-WEB-23, FR-WEB-26), a mission still running (FR-ING-15), the pilot's health and the aircraft's damage on
the sortie page (OQ-115), a column description opened by a finger.

Same conventions as `test_flows.py`. The shared e2e world has no running mission and no ditching, so the tests set that
state on one row through `tweak_data` (restored after each test); what an admin saves through the admin pages is
restored the same way (the whole site settings value is put back).
"""

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e import flow_helpers
from tests.e2e.conftest import TweakData
from tests.e2e.flow_helpers import admin_login, no_horizontal_scroll
from tests.e2e.helpers import expect_heading, main_region
from tests.e2e.world import World

patient_timeouts = flow_helpers.patient_timeouts
pytestmark = pytest.mark.usefixtures("patient_timeouts")

OWN_QUIP = "The meadow filed a formal complaint (e2e)."
RENAMED = "Skybreaker (e2e)"
RENAMED_DESCRIPTION = "Shoot down aircraft, all sorties together (e2e)."
SWITCHED_OFF = "Regular"  # Ace holds several tiers of it
BUILT_IN = "Sky Hunter"  # what the renamed achievement is called by default


def save_admin_form(page: Page, saved: str) -> None:
    page.locator("#content-main").get_by_role("button", name="Save").click()
    expect(page.get_by_text(saved, exact=True).first).to_be_visible()


# --- admin quips ------------------------------------------------------------------------------------------------------


def test_an_admin_adds_an_own_quip_and_a_visitor_sees_it_on_a_sortie(
    page: Page, world: World, tweak_data: TweakData
) -> None:
    """/admin/quips/: on the "ditched" spot show only an own line, add it; the sortie that ended in a ditching
    carries exactly that line; turning every quip off removes it."""
    tweak_data("SiteSettings", None, {"quips": {}, "quips_enabled": True})  # restores what an admin save changes
    tweak_data("PlayerSortie", world.delta_sortie_pk, {"outcome": "ditched"})
    sortie_url = f"/sorties/{world.delta_sortie_pk}/"

    page.goto(sortie_url)
    built_in = page.locator("p.flavor--sortie")
    expect(built_in).to_have_count(1)  # the built-in line of the spot
    assert OWN_QUIP not in built_in.inner_text()

    admin_login(page)
    page.goto("/admin/quips/")
    spot = page.locator("#spot-sortie_ditched")
    spot.get_by_label("Quips here").select_option("custom_only")
    spot.get_by_label("New quip text").fill(OWN_QUIP)
    save_admin_form(page, "Quips saved.")
    spot = page.locator("#spot-sortie_ditched")  # the saved line is now a stored one of the spot
    expect(spot.get_by_label("Quip text", exact=True)).to_have_value(OWN_QUIP)
    expect(spot.get_by_text("Shows 1 line now.")).to_be_visible()

    page.goto(sortie_url)
    expect(page.locator("p.flavor--sortie")).to_have_text(OWN_QUIP)
    expect_heading(page, world.delta, level=1)

    page.goto("/admin/quips/")  # the global switch beats every spot
    page.get_by_label("Show quips on the site").uncheck()
    save_admin_form(page, "Quips saved.")
    page.goto(sortie_url)
    expect(main_region(page).get_by_role("heading", level=1)).to_be_visible()
    expect(page.locator("p.flavor--sortie")).to_have_count(0)


# --- admin achievements -----------------------------------------------------------------------------------------------


def test_an_admin_renames_one_achievement_and_switches_another_off(
    page: Page, world: World, tweak_data: TweakData
) -> None:
    """/admin/achievements/: rename one (name and description, English) and switch one off; the pilot's achievements,
    his profile and the overview show the new name, and the switched-off one is gone everywhere."""
    tweak_data("SiteSettings", None, {"achievements": {}})
    achievements_url = f"/players/{world.ace_pk}/achievements/"

    page.goto(achievements_url)
    expect(main_region(page).get_by_text(BUILT_IN, exact=True).first).to_be_visible()
    expect(main_region(page).get_by_text(SWITCHED_OFF, exact=True).first).to_be_visible()

    admin_login(page)
    page.goto("/admin/achievements/")
    renamed = page.locator("#achievement-career_kills")
    renamed.get_by_text("Own name and description per language").click()
    renamed.get_by_label("Name in English").fill(RENAMED)
    renamed.get_by_label("Description in English").fill(RENAMED_DESCRIPTION)
    page.locator("#achievement-regular").get_by_label("Switched on").uncheck()
    save_admin_form(page, "Achievements saved.")
    expect(page.locator("#achievement-career_kills").get_by_role("heading", level=2)).to_contain_text("changed")

    for url in (achievements_url, "/achievements/"):
        page.goto(url)
        expect(main_region(page).get_by_text(RENAMED, exact=True).first).to_be_visible()
        expect(main_region(page).get_by_text(BUILT_IN, exact=True)).to_have_count(0)
        expect(main_region(page).get_by_text(SWITCHED_OFF, exact=True)).to_have_count(0)
    page.goto("/achievements/")
    expect(main_region(page).get_by_text(RENAMED_DESCRIPTION)).to_be_visible()
    page.goto(f"/players/{world.ace_pk}/")
    expect(main_region(page).get_by_text(SWITCHED_OFF, exact=True)).to_have_count(0)


# --- a mission still running ------------------------------------------------------------------------------------------


def test_a_running_mission_says_so_on_the_lists_the_mission_and_the_sortie(
    page: Page, world: World, tweak_data: TweakData
) -> None:
    """FR-ING-15: a provisional mission is badged "Live" in the mission list, says "Mission still running" on its own
    page and on its sorties' pages; once it ends none of that is left."""
    mission_url = f"/missions/{world.featured_mission_pk}/"
    sortie_url = f"/sorties/{world.ace_sortie_pk}/"
    still_running = "The numbers may still change until it ends."

    def expect_running(running: bool) -> None:
        count = 1 if running else 0
        for url in (mission_url, sortie_url):
            page.goto(url)
            expect(main_region(page).get_by_text(still_running)).to_have_count(count)
        page.goto("/missions/")
        expect(main_region(page).get_by_text("Live", exact=True)).to_have_count(count)

    expect_running(False)
    tweak_data("Mission", world.featured_mission_pk, {"is_live": True, "completed_cleanly": False})
    expect_running(True)
    page.goto(mission_url)
    expect(main_region(page).get_by_text("Mission still running")).to_be_visible()
    expect(main_region(page).get_by_text("Log incomplete")).to_have_count(0)  # running is not "incomplete"
    page.goto(f"/sorties/{world.ace_sortie_pk}/")
    expect_heading(page, world.ace, level=1)  # the numbers are still there, only flagged
    expect(main_region(page).locator(".sortie-head__badges").get_by_text("Live", exact=True)).to_be_visible()
    banner_y = main_region(page).get_by_text("Mission still running").bounding_box()
    tiles_y = main_region(page).locator(".stat-tiles").first.bounding_box()
    assert banner_y is not None
    assert tiles_y is not None
    assert banner_y["y"] < tiles_y["y"]  # warned before the numbers


# --- pilot health and aircraft damage ---------------------------------------------------------------------------------


def test_the_sortie_page_shows_the_pilots_health_and_the_aircraft_damage(page: Page, world: World) -> None:
    """OQ-115: a landing intact reads 100% health and 0% damage; a pilot shot down dead 0% health and a destroyed
    aircraft 100% damage; a bailout 100% health (the pilot is fine) and 100% damage."""

    def readings(sortie_pk: int) -> tuple[str, str]:
        page.goto(f"/sorties/{sortie_pk}/")
        health = main_region(page).locator("dt", has_text="Pilot health").locator("xpath=following-sibling::dd[1]")
        damage = main_region(page).locator("dt", has_text="Aircraft damage").locator("xpath=following-sibling::dd[1]")
        return health.inner_text().strip(), damage.inner_text().strip()

    assert readings(world.ace_sortie_pk) == ("100%", "0%")
    assert readings(world.delta_sortie_pk) == ("0%", "100%")
    bob_sortie = world.ace_sortie_pk + 1  # the mission's second sortie: Bob Wingman, bailed out
    page.goto(f"/sorties/{bob_sortie}/")
    expect_heading(page, world.bob, level=1)
    assert readings(bob_sortie) == ("100%", "100%")


# --- a description opened by a finger ---------------------------------------------------------------------------------


def test_a_tap_opens_a_column_description_on_a_phone_and_a_tap_elsewhere_closes_it(
    browser: Browser, base_url: str
) -> None:
    """A touch screen has no hover: tapping the "?" of a sortable header shows its description and does not sort;
    tapping the heading closes it."""
    context = browser.new_context(
        base_url=base_url,
        viewport={"width": 390, "height": 844},
        has_touch=True,
        is_mobile=True,
    )
    page = context.new_page()
    page.set_default_timeout(flow_helpers.PATIENT_MS)
    try:
        page.goto("/leaderboards/ground-hour/")
        url = page.url
        header = page.get_by_role("columnheader", name="Time on target")
        tip = header.get_by_role("tooltip")
        expect(tip).to_be_hidden()

        header.locator(".col-hint__marker").tap()
        expect(tip).to_be_visible()
        expect(tip).to_contain_text("attacking enemy ground targets")
        box = tip.bounding_box()
        assert box is not None
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= 390
        assert page.url == url, "tapping the description sorted the table"
        assert no_horizontal_scroll(page)

        page.get_by_role("heading", level=1).tap()
        expect(tip).to_be_hidden()
    finally:
        context.close()
