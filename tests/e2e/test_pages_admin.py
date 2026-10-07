"""Markdown pages (roadmap 0.2.0, OQ-133): an admin writes a page with a preview, links it from the navigation, and a
visitor opens it from the nav link; a translation shows to a visitor of that language and the base text to the rest."""

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e import flow_helpers
from tests.e2e.conftest import TweakData
from tests.e2e.flow_helpers import admin_login
from tests.e2e.helpers import main_region

patient_timeouts = flow_helpers.patient_timeouts
pytestmark = pytest.mark.usefixtures("patient_timeouts")


def test_an_admin_writes_a_page_links_it_and_a_visitor_opens_it(page: Page, tweak_data: TweakData) -> None:
    tweak_data("SiteSettings", None, {"links": []})  # an admin save of the settings rewrites the published links
    admin_login(page)

    # create the page with a German translation and look at the preview
    page.goto("/admin/il2ks_db/page/add/")
    page.locator("#id_title").fill("Server rules")
    page.locator("#id_title").press("Tab")  # leaving the title fills the address from it
    expect(page.locator("#id_slug")).to_have_value("server-rules")
    page.locator("#id_source").fill("# Be nice\n\nFly **safe**.\n\n<script>alert(1)</script>")
    page.get_by_role("button", name="Preview").first.click()
    preview = page.locator(".md-preview").first
    expect(preview.locator("h1")).to_have_text("Be nice")
    expect(preview.locator("strong")).to_have_text("safe")
    assert preview.locator("script").count() == 0
    page.get_by_text("Add another Translation").click()
    page.locator("#id_page_translations-0-language").select_option("de")
    page.locator("#id_page_translations-0-title").fill("Serverregeln")
    page.locator("#id_page_translations-0-source").fill("# Sei nett")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("was added successfully")).to_be_visible()

    try:
        # link it from the navigation
        page.goto("/admin/il2ks_db/sitesettings/1/change/")
        page.locator("#id_nav_links-0-label").fill("Rules")
        page.locator("#id_nav_links-0-page").select_option(label="Server rules")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_text("was changed successfully")).to_be_visible()

        # a visitor follows the link, in English and then in German
        page.goto("/")
        page.locator(".site-nav").get_by_role("link", name="Rules").click()
        expect(page).to_have_url(re.compile(r"/p/server-rules/$"))
        expect(main_region(page).get_by_role("heading", name="Be nice")).to_be_visible()
        expect(page.locator(".site-nav").get_by_role("link", name="Rules")).to_have_attribute("aria-current", "page")
        page.goto("/language/?language=de&next=/p/server-rules/")
        expect(main_region(page).get_by_role("heading", name="Sei nett")).to_be_visible()
        page.goto("/language/?language=fr&next=/p/server-rules/")
        expect(main_region(page).get_by_role("heading", name="Be nice")).to_be_visible()
    finally:
        page.goto("/language/?language=en&next=/")
        page.goto("/admin/il2ks_db/page/")
        page.get_by_role("link", name="Server rules").click()
        page.get_by_role("link", name="Delete").click()
        page.get_by_role("button", name=re.compile("Yes, I.m sure")).click()
        expect(page.get_by_text("was deleted successfully")).to_be_visible()
    page.goto("/")
    expect(page.locator(".site-nav").get_by_role("link", name="Rules")).to_have_count(0)
