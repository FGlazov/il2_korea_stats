"""A wrapped row never leaves one item alone on its last line (maintainer 2026-11-02, stats.yalu.info at 1440p: the
menu wrapped with "Discord" by itself under the other four). Sweeps the widths, because a row breaks where they say."""

from collections.abc import Callable

from playwright.sync_api import Page

from tests.e2e.helpers import header_search
from tests.e2e.world import World

type SetBranding = Callable[[dict[str, object]], None]

YALU = {"site_title": "Yalu Express Stats", "server_name": "Yalu Express - Dynamic Campaign"}
LINKS = [
    ["Server Info", "https://info.example/", ""],
    ["Discord", "https://discord.example/", "discord"],
    ["Forum", "https://forum.example/", "forum"],
    ["Patreon", "https://patreon.example/", "patreon"],
    ["Wiki", "https://wiki.example/", ""],
    ["Rules", "https://rules.example/", ""],
]
WIDTHS = range(360, 1921, 40)

ROWS = """selector => {
  const rows = new Map();
  for (const el of document.querySelectorAll(selector)) {
    const top = Math.round(el.getBoundingClientRect().top);
    rows.set(top, (rows.get(top) || 0) + 1);
  }
  return [...rows.entries()].sort((a, b) => a[0] - b[0]).map(entry => entry[1]);
}"""


def lone_last_rows(page: Page, selector: str) -> list[tuple[int, list[int]]]:
    found: list[tuple[int, list[int]]] = []
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        rows: list[int] = page.evaluate(ROWS, selector)
        if len(rows) > 1 and rows[-1] == 1:
            found.append((width, rows))
    return found


def test_the_menu_never_wraps_to_one_lone_item(page: Page, set_branding: SetBranding) -> None:
    for count in range(1, len(LINKS) + 1):
        set_branding({**YALU, "links": LINKS[:count]})
        page.goto("/")
        header_search(page).wait_for()
        page.evaluate("document.fonts.ready.then(() => 0)")
        assert lone_last_rows(page, ".site-nav > ul > li") == [], f"{count} custom links"


def test_the_page_links_never_wrap_to_one_lone_item(page: Page, world: World) -> None:
    for url, selector in (
        (f"/players/{world.ace_pk}/", ".profile-nav > a"),
        ("/leaderboards/", ".board-tabs__group:first-child li"),
        ("/achievements/life_kills/?tour=1", ".medal-tabs > a"),
    ):
        page.goto(url)
        page.evaluate("document.fonts.ready.then(() => 0)")
        assert lone_last_rows(page, selector) == [], url
