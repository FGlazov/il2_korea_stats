"""The header navigation with the admin's own links (TD-25): 1-6 custom links at common widths.

Measured result (also in the admin help text, `RECOMMENDED_NAV_LINKS`, and docs/customizing.md): the header never gets
a horizontal scrollbar and no link leaves the screen at any width, because the menu wraps onto further rows. How many
links share the row with the built-in four depends on the width and on the labels; the recommended number is the count
that keeps the header at its normal height at 1280 px and 1920 px (the page is at most 1240 px wide) with the default
title and short labels. Long labels ("Join our Discord server") leave room for one link; at 360 px two short ones fit.

How many links fit on one row depends on the font, and the default body text uses the viewer's system font (Segoe UI on
Windows, DejaVu or Liberation on a Linux runner), so the counts differ per OS. The test therefore asserts the invariants
that hold for every font (no scrollbar, no link off screen, the header never overlaps the page), and checks the
"recommended maximum fits on one row" promise with the bundled Barlow Condensed uploaded as the body font, which makes
the measurement the same everywhere.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page

from il2ks.web.site_forms import RECOMMENDED_NAV_LINKS
from tests.e2e.helpers import header_search

type SetBranding = Callable[[dict[str, object]], None]
WIDTHS = (360, 768, 1280, 1920)
VENDOR_FONTS = Path(__file__).resolve().parents[2] / "src" / "il2ks" / "web" / "static" / "il2ks" / "vendor"
BUNDLED_FONTS = {
    "heading": str(VENDOR_FONTS / "BarlowCondensed-700.woff2"),
    "body": str(VENDOR_FONTS / "BarlowCondensed-600.woff2"),
}
SHORT = [
    ["Discord", "https://discord.example/a", "discord"],
    ["Forum", "https://forum.example/", "forum"],
    ["Patreon", "https://patreon.example/", "patreon"],
    ["Wiki", "https://wiki.example/", ""],
    ["Rules", "https://rules.example/", ""],
    ["Shop", "https://shop.example/", "link"],
]
LONG = [
    ["Join our Discord server", "https://discord.example/a", "discord"],
    ["Community forum", "https://forum.example/", "forum"],
    ["Support us on Patreon", "https://patreon.example/", "patreon"],
    ["Server rules and wiki", "https://wiki.example/", ""],
    ["TeamSpeak channel", "https://ts.example/", ""],
    ["Merchandise shop", "https://shop.example/", "link"],
]

MEASURE = """() => {
  const nav = document.querySelector('.site-nav');
  const items = [...nav.querySelectorAll(':scope > ul > li')];
  const rows = new Set(items.map(li => Math.round(li.getBoundingClientRect().top)));
  const header = document.querySelector('header.site-header').getBoundingClientRect();
  const main = document.querySelector('#main').getBoundingClientRect();
  return {
    headerBottom: Math.round(header.bottom),
    mainTop: Math.round(main.top),
    items: items.length,
    rows: rows.size,
    header: Math.round(header.height),
    scrollOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    maxRight: Math.max(...items.map(li => li.getBoundingClientRect().right)),
    minLeft: Math.min(...items.map(li => li.getBoundingClientRect().left)),
    width: window.innerWidth,
  };
}"""


def measure(page: Page, width: int) -> dict[str, int]:
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("/")
    header_search(page).wait_for()
    page.evaluate("document.fonts.ready.then(() => 0)")  # font-display: swap would otherwise measure the fallback
    return page.evaluate(MEASURE)


def check_invariants(got: dict[str, int], count: int, width: int) -> None:
    where = f"{count} links at {width}px: {got}"
    assert got["items"] >= 4 + count, where
    assert got["scrollOverflow"] <= 0, f"horizontal scrollbar, {where}"
    assert got["minLeft"] >= 0, f"a link is off screen, {where}"
    assert got["maxRight"] <= width, f"a link is off screen, {where}"
    assert got["headerBottom"] <= got["mainTop"], f"the header overlaps the page, {where}"


@pytest.mark.parametrize("labels", [SHORT, LONG], ids=["short-labels", "long-labels"])
def test_header_holds_up_with_one_to_six_custom_links(
    page: Page, set_branding: SetBranding, labels: list[list[str]]
) -> None:
    """Whatever the system font: any number of links wraps instead of overflowing or covering the page."""
    for count in range(1, 7):
        set_branding({"links": labels[:count]})
        for width in WIDTHS:
            got = measure(page, width)
            assert got["items"] == 4 + count
            check_invariants(got, count, width)


def test_recommended_links_stay_on_one_row_on_a_desktop(page: Page, set_branding: SetBranding) -> None:
    """The promise in the admin help text: up to RECOMMENDED_NAV_LINKS short links do not make the header taller at
    1280 px and 1920 px. Measured with the bundled font so that the result does not depend on the OS."""
    set_branding({"links": [], "font_files": BUNDLED_FONTS})
    baseline = {width: measure(page, width) for width in (1280, 1920)}
    report: dict[str, dict[int, int]] = {}
    for count in range(1, 7):
        set_branding({"links": SHORT[:count], "font_files": BUNDLED_FONTS})
        for width in (1280, 1920):
            got = measure(page, width)
            check_invariants(got, count, width)
            one_row = got["header"] <= baseline[width]["header"] and got["rows"] <= baseline[width]["rows"]
            report.setdefault(str(width), {})[count] = int(one_row)
    print("\nlayout (1 = the header kept its height):", json.dumps(report))
    for width in (1280, 1920):
        assert all(report[str(width)][n] for n in range(1, RECOMMENDED_NAV_LINKS + 1)), report


def test_many_links_wrap_instead_of_overflowing(page: Page, set_branding: SetBranding) -> None:
    many = [[f"Link number {n}", f"https://example.org/{n}", ""] for n in range(1, 13)]
    set_branding({"links": many})
    for width in WIDTHS:
        got = measure(page, width)
        check_invariants(got, 12, width)
        assert got["rows"] >= 2
