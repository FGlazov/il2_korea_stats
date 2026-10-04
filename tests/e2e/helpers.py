"""Small helpers for the browser tests: what a visitor would do, in one line.

Tests find things the way a person does (role, label, visible text), never through CSS classes or ids, so a redesign
that keeps the words keeps the tests. The one exception is `sortie_links`: a link is identified by where it goes."""

import re

from playwright.sync_api import Locator, Page, expect

FULL_RELOAD_FLAG = "__il2ksNoReload"


def header_search(page: Page) -> Locator:
    """The "Find a player" box in the site header (every page has it)."""
    return page.get_by_role("banner").get_by_role("searchbox", name="Find a player")


def link_or_button(page: Page, name: str) -> Locator:
    """A call to action: the theme styles some links as buttons (`role="button"`); a visitor can't tell them apart."""
    return page.get_by_role("link", name=name).or_(page.get_by_role("button", name=name))


def main_region(page: Page) -> Locator:
    return page.get_by_role("main")


def sortie_links(page: Page) -> Locator:
    """Links to sortie pages (`/sorties/<id>/`), wherever they are shown (a table row's date, an aircraft name...)."""
    return page.locator("a[href^='/sorties/']")


SETTLED_JS = (
    "document.readyState === 'complete' && document.fonts.status === 'loaded'"
    " && !document.querySelector('.htmx-request')"
)


def wait_until_settled(page: Page) -> None:
    """The page is loaded, its fonts are in and no htmx request is in flight (the "online now" fragment, a swap).

    Use instead of `wait_until="networkidle"`: that waits for 500 ms without any request, which a loaded machine (or a
    polling fragment) can keep from ever happening within the timeout. This waits for the actual state."""
    page.wait_for_function(SETTLED_JS)


def mark_page(page: Page) -> None:
    """Remember this document; `expect_same_document` fails when htmx (or anything) replaced it with a full load."""
    page.evaluate(f"window.{FULL_RELOAD_FLAG} = true")


def expect_same_document(page: Page) -> None:
    """The page was updated in place (htmx swap), not reloaded: the flag set by `mark_page` is still there."""
    assert page.evaluate(f"window.{FULL_RELOAD_FLAG} === true"), "the page reloaded instead of updating in place"


def expect_heading(page: Page, name: str | re.Pattern[str], level: int | None = None) -> None:
    expect(page.get_by_role("heading", name=name, level=level).first).to_be_visible()
