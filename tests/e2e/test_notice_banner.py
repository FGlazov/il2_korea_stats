"""The site-wide notice banner and the sortie timeline's altitude column in a real browser (roadmap 0.2.0)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.flow_helpers import no_horizontal_scroll
from tests.e2e.world import World

type SetBranding = Callable[[dict[str, object]], None]


def test_the_banner_shows_under_the_header_and_escapes_its_text(page: Page, set_branding: SetBranding) -> None:
    set_branding({"notice": {"text": "Event <b>tonight</b> at 20:00", "level": "warning"}})

    page.goto("/players/")

    banner = page.locator(".site-notice .notice--warning")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("Event <b>tonight</b> at 20:00")  # shown as typed, not as markup
    header = page.locator("header.site-header").bounding_box()
    main = page.locator("main#main").bounding_box()
    box = banner.bounding_box()
    assert header is not None
    assert main is not None
    assert box is not None
    assert header["y"] + header["height"] <= box["y"] + 1  # under the header
    assert box["y"] + box["height"] <= main["y"] + 1  # above the page content


@pytest.mark.parametrize("width", [360, 1280])
def test_a_long_banner_fits_the_screen(page: Page, set_branding: SetBranding, width: int) -> None:
    set_branding({"notice": {"text": "Maintenance tonight, " + "the server is down for a while " * 8}})
    page.set_viewport_size({"width": width, "height": 800})

    page.goto("/")

    expect(page.locator(".site-notice")).to_be_visible()
    assert no_horizontal_scroll(page)


def test_an_expired_banner_disappears_from_a_page_the_browser_kept(page: Page, set_branding: SetBranding) -> None:
    """The browser revalidates (304) and reuses the old page while nothing changed: the expiry passing must hide the
    banner anyway (localtime.js, like the "next tour starts" line)."""
    until = datetime.now(UTC) + timedelta(seconds=4)
    set_branding({"notice": {"text": "Soon over", "until": until.isoformat()}})

    page.goto("/players/")
    expect(page.locator(".site-notice")).to_be_visible()
    page.wait_for_timeout(5000)  # the expiry passes; nothing was saved, so the data version (and the ETag) stay
    page.reload()
    expect(page.locator(".site-notice")).to_be_hidden()


def test_the_timeline_shows_an_altitude_column_without_overflowing_a_phone(page: Page, world: World) -> None:
    page.set_viewport_size({"width": 360, "height": 800})

    page.goto(f"/sorties/{world.ace_sortie_pk}/")

    header = page.locator("table.timeline thead th", has_text="Altitude")
    expect(header).to_be_visible()
    assert no_horizontal_scroll(page)
