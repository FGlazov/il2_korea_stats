"""The header navigation with the admin's own links (TD-25): 1-6 custom links at common widths.

Measured result (also in the admin help text, `RECOMMENDED_NAV_LINKS`, and docs/customizing.md): the header never gets
a horizontal scrollbar and no link leaves the screen at any width, because the menu wraps onto further rows. How many
links share the row with the built-in four depends on the width and on the labels; the recommended number is the count
that keeps the header at its normal height at 1280 px and 1920 px (the page is at most 1240 px wide) with the default
title and short labels. Long labels ("Join our Discord server") leave room for one link; at 360 px two short ones fit.
"""

import json
from collections.abc import Callable

import pytest
from playwright.sync_api import Page

from il2ks.web.site_forms import RECOMMENDED_NAV_LINKS
from tests.e2e.helpers import header_search

type SetBranding = Callable[[dict[str, object]], None]
WIDTHS = (360, 768, 1280, 1920)
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
  return {
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
    return page.evaluate(MEASURE)


@pytest.mark.parametrize("labels", [SHORT, LONG], ids=["short-labels", "long-labels"])
def test_header_holds_up_with_one_to_six_custom_links(
    page: Page, set_branding: SetBranding, labels: list[list[str]]
) -> None:
    baseline: dict[int, dict[str, int]] = {}
    set_branding({"links": []})
    for width in WIDTHS:
        baseline[width] = measure(page, width)
    report: dict[str, dict[int, int]] = {}
    for count in range(1, 7):
        set_branding({"links": labels[:count]})
        for width in WIDTHS:
            got = measure(page, width)
            assert got["items"] == 4 + count
            assert got["scrollOverflow"] <= 0, f"{count} links at {width}px: horizontal scrollbar {got}"
            assert got["minLeft"] >= 0, f"{count} links at {width}px: a link is off screen {got}"
            assert got["maxRight"] <= width, f"{count} links at {width}px: a link is off screen {got}"
            one_row = got["header"] <= baseline[width]["header"] and got["rows"] <= baseline[width]["rows"]
            report.setdefault(str(width), {})[count] = int(one_row)
    print("\nlayout (1 = the header kept its height):", json.dumps(report))
    # The promise in the admin help text: up to RECOMMENDED_NAV_LINKS extra links do not make the header taller on a
    # desktop screen with short labels.
    if labels is SHORT:
        for width in (1280, 1920):
            assert all(report[str(width)][n] for n in range(1, RECOMMENDED_NAV_LINKS + 1)), report


def test_many_links_wrap_instead_of_overflowing(page: Page, set_branding: SetBranding) -> None:
    many = [[f"Link number {n}", f"https://example.org/{n}", ""] for n in range(1, 13)]
    set_branding({"links": many})
    for width in WIDTHS:
        got = measure(page, width)
        assert got["scrollOverflow"] <= 0, f"12 links at {width}px: {got}"
        assert got["maxRight"] <= width, got
        assert got["rows"] >= 2
