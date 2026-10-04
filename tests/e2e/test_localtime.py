"""Viewer-local times in a real browser (FR-WEB-17): a Tokyo and a Berlin viewer see their own zone."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import Browser, BrowserContext, Page, expect

from tests.e2e.world import World


def viewer(browser: Browser, base_url: str, timezone_id: str, locale: str = "en-US") -> tuple[BrowserContext, Page]:
    context = browser.new_context(base_url=base_url, timezone_id=timezone_id, locale=locale)
    return context, context.new_page()


def expected(iso: str, zone: str, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(zone)).strftime(fmt)


def test_mission_start_follows_the_viewer_zone(browser: Browser, base_url: str, world: World) -> None:
    url = f"/missions/{world.featured_mission_pk}/"
    seen: dict[str, str] = {}
    for zone in ("Asia/Tokyo", "Europe/Berlin"):
        context, page = viewer(browser, base_url, zone)
        try:
            page.goto(url)
            first = page.locator("time[data-il2-time='datetime']").first
            expect(first).to_have_attribute("data-il2-local", zone)
            iso = first.get_attribute("datetime")
            assert iso is not None
            expect(first).to_have_text(expected(iso, zone))
            assert "UTC" in (first.get_attribute("title") or "")
            expect(page.locator("[data-il2-tz-note]")).to_contain_text(zone)
            seen[zone] = first.inner_text()
        finally:
            context.close()
    assert seen["Asia/Tokyo"] != seen["Europe/Berlin"]  # 7 or 8 hours apart, never the same text


def test_without_javascript_the_page_says_utc(browser: Browser, base_url: str, world: World) -> None:
    context = browser.new_context(base_url=base_url, java_script_enabled=False, timezone_id="Asia/Tokyo")
    try:
        page = context.new_page()
        page.goto(f"/missions/{world.featured_mission_pk}/")
        expect(page.locator("time[data-il2-time='datetime']").first).to_contain_text("UTC")
        expect(page.locator("[data-il2-tz-note]")).to_have_text("Times are shown in UTC.")
    finally:
        context.close()


def test_times_in_a_list_swapped_in_by_htmx_are_converted(browser: Browser, base_url: str) -> None:
    context, page = viewer(browser, base_url, "Asia/Tokyo")
    try:
        page.goto("/missions/")
        first = page.locator("tbody time[data-il2-time='date']").first
        expect(first).to_have_attribute("data-il2-local", "Asia/Tokyo")
        page.get_by_role("link", name=re.compile(r"^2$")).first.click()  # page 2: an htmx swap
        swapped = page.locator("tbody time[data-il2-time='date']").first
        expect(swapped).to_have_attribute("data-il2-local", "Asia/Tokyo")
        assert not page.locator("tbody time[data-il2-time]:not([data-il2-local])").count()
    finally:
        context.close()


def test_game_world_time_is_left_alone(browser: Browser, base_url: str, world: World) -> None:
    context, page = viewer(browser, base_url, "Asia/Tokyo")
    try:
        page.goto(f"/sorties/{world.ace_sortie_pk}/")
        # the in-mission clock and the "+m:ss" offsets are plain text, never <time> elements
        assert page.locator("time:not([data-il2-time])").count() == 0
    finally:
        context.close()
