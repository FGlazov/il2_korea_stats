"""Small helpers for the browser tests: what a visitor would do, in one line.

Tests find things the way a person does (role, label, visible text), never through CSS classes or ids, so a redesign
that keeps the words keeps the tests. The one exception is `sortie_links`: a link is identified by where it goes."""

import re

import pytest
from playwright.sync_api import Locator, Page, expect

PAGES_PENDING = pytest.mark.xfail(strict=False, reason="pages not merged yet (doc 08): remove once they are")
"""Marks the tests that need the real pages (home aside). Grep for it when the pages land: it is the to-do list."""

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


def mark_page(page: Page) -> None:
    """Remember this document; `expect_same_document` fails when htmx (or anything) replaced it with a full load."""
    page.evaluate(f"window.{FULL_RELOAD_FLAG} = true")


def expect_same_document(page: Page) -> None:
    """The page was updated in place (htmx swap), not reloaded: the flag set by `mark_page` is still there."""
    assert page.evaluate(f"window.{FULL_RELOAD_FLAG} === true"), "the page reloaded instead of updating in place"


def expect_heading(page: Page, name: str | re.Pattern[str], level: int | None = None) -> None:
    expect(page.get_by_role("heading", name=name, level=level).first).to_be_visible()
