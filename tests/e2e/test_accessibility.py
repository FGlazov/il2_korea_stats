"""Accessibility (doc 16 "Accessibility", target WCAG 2.1 AA for every page): axe-core on every public page, in light
and dark, at 320 px (WCAG 1.4.10 reflow: a 1280 px window zoomed to 400 %) and desktop width, with the `<details>`
menus (columns, language) opened too; the error pages and the admin pages as well.

axe-core comes from `axe-playwright-python` (a dev-group dependency that bundles `axe.min.js`): the test injects it,
the site never ships it and nothing loads from a CDN. Run only this file with
`IL2KS_TEST_E2E=1 uv run pytest tests/e2e/test_accessibility.py -m e2e`. A failure lists the rule, the impact and
the offending elements. Rules that cannot be fixed without a product decision go in `ALLOWED` with the reason.
"""

from typing import Any, cast

import pytest
from axe_playwright_python.sync_playwright import Axe  # pyright: ignore[reportMissingTypeStubs]
from playwright.sync_api import Browser, Page

from tests.e2e.helpers import wait_until_settled
from tests.e2e.test_visual_qa import ADMIN_PAGES, OPEN_DETAILS_JS, PAGES, login, with_theme
from tests.e2e.world import World

WIDTHS = (320, 1280)
SCHEMES = ("light", "dark")
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]

ALLOWED: dict[str, str] = {}
"""axe rule id -> why the site may break it. Empty: nothing is waived."""

ADMIN_ALLOWED: dict[str, str] = {
    "empty-table-header": "Django's own tabular inline (the navigation links) leaves the header of its 'original' "
    "column empty; changing it means copying Django's whole template (a minor, best-practice rule).",
}
"""The same, for the admin pages only."""

PUBLIC = [name for name in PAGES if name != "styleguide"]
"""The debug-only style guide is not a public page."""

ADMIN = {
    **ADMIN_PAGES,
    "admin-branding": "/admin/il2ks_db/sitesettings/1/change/",
    "admin-quips": "/admin/quips/",
    "admin-achievements": "/admin/achievements/",
    "admin-score": "/admin/score/",
    "admin-rules": "/admin/rules/",
    "admin-tours": "/admin/tours/",
    "admin-leaderboards": "/admin/leaderboards/",
    "admin-pages": "/admin/il2ks_db/page/",
    "admin-page-add": "/admin/il2ks_db/page/add/",
    "admin-players": "/admin/il2ks_db/player/",
}
"""The admin pages il2ks adds or styles itself (Django's own change lists are covered through a few examples)."""

REFLOW_JS = """() => {
  const doc = document.documentElement, overflow = doc.scrollWidth - doc.clientWidth;
  const inScroller = el => { for (let a = el.parentElement; a && a !== document.body; a = a.parentElement)
    if (['auto', 'scroll', 'hidden', 'clip'].includes(getComputedStyle(a).overflowX)) return true; return false; };
  const wide = overflow > 1 ? [...document.body.querySelectorAll('*')].filter(el =>
    el.getBoundingClientRect().right > doc.clientWidth + 1 && !inScroller(el)) : [];
  const outermost = wide.filter(el => !wide.includes(el.parentElement)).slice(0, 4);
  const label = el => el.tagName.toLowerCase() + '.' + String(el.className).trim().split(/\\s+/).join('.');
  return {overflow, culprits: outermost.map(label)};
}"""
"""How far the page scrolls sideways, and the outermost elements that stick out. A wide table scrolls in its own
wrapper, which WCAG 1.4.10 allows."""


def axe_violations(page: Page, label: str, allowed: dict[str, str] = ALLOWED) -> list[str]:
    """axe-playwright-python ships no type information: the untyped call is cast to the JSON shape axe documents."""
    options = {"runOnly": {"type": "tag", "values": TAGS}, "resultTypes": ["violations"]}
    results = Axe().run(page, options=options)  # pyright: ignore[reportUnknownMemberType]
    found = cast(list[dict[str, Any]], results.response["violations"])  # pyright: ignore[reportUnknownMemberType]
    lines: list[str] = []
    for violation in found:
        if violation["id"] in allowed:
            continue
        targets = "; ".join(" ".join(str(t) for t in node["target"]) for node in violation["nodes"][:5])
        lines.append(f"[{label}] {violation['id']} ({violation['impact']}, {len(violation['nodes'])}x): {targets}")
    return lines


def audit(page: Page, url: str, name: str, allowed: dict[str, str] = ALLOWED) -> list[str]:
    """axe in both schemes at both widths (menus closed and open), plus the reflow check at 320 px."""
    found: list[str] = []
    for scheme in SCHEMES:
        page.emulate_media(color_scheme=scheme)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(with_theme(url, scheme))
            wait_until_settled(page)
            tag = f"{name} {width}px {scheme}"
            found += axe_violations(page, tag, allowed)
            if width == min(WIDTHS) and (reflow := page.evaluate(REFLOW_JS))["overflow"] > 1:
                wide = " | ".join(reflow["culprits"])
                found.append(f"[{tag}] reflow: the page scrolls sideways by {reflow['overflow']}px; wide: {wide}")
            if page.locator("details").count():
                page.evaluate(OPEN_DETAILS_JS)
                page.wait_for_timeout(450)  # Pico fades a dropdown in
                found += axe_violations(page, tag + " open", allowed)
    return found


@pytest.mark.parametrize("name", PUBLIC)
def test_public_page_has_no_axe_violations(page: Page, world: World, name: str) -> None:
    """axe-core finds nothing (WCAG 2.1 A/AA + best practices) on `name`: 2 schemes x 2 widths, menus open too, and
    the page does not scroll sideways at 320 px."""
    url = PAGES[name](page, world)
    found = audit(page, url, name)
    assert not found, f"{url}\n" + "\n".join(sorted(set(found)))


@pytest.mark.allow_console_errors  # the browser logs the 404 / 500 status itself
@pytest.mark.parametrize("code", [404, 500])
def test_the_error_pages_have_no_axe_violations(page: Page, code: int) -> None:
    """The site's own 404.html and 500.html through the DEBUG-only preview: the e2e server runs with DEBUG, where an
    unknown address shows Django's technical 404 instead."""
    found = audit(page, f"/_errors/{code}/", str(code))
    assert page.locator(".error-page").count() == 1
    assert not found, "\n".join(sorted(set(found)))


def test_the_admin_login_page_has_no_axe_violations(page: Page) -> None:
    found = audit(page, "/admin/login/", "admin-login")
    assert not found, "\n".join(sorted(set(found)))


@pytest.mark.parametrize("name", ADMIN)
def test_admin_page_has_no_axe_violations(page: Page, name: str) -> None:
    """The site owner's pages: the same checks. Admin has its own theme switch, so only the browser's scheme changes
    (the `?theme=` the audit adds is ignored there)."""
    login(page)
    found = audit(page, ADMIN[name], name, ADMIN_ALLOWED)
    assert not found, f"{ADMIN[name]}\n" + "\n".join(sorted(set(found)))


@pytest.mark.parametrize("language", ["de", "ru", "fr", "es", "pt-BR"])
def test_every_language_has_its_lang_attribute_and_no_axe_violations(
    browser: Browser, base_url: str, world: World, language: str
) -> None:
    """The browser asks for `language`: `<html lang>` follows it (WCAG 3.1.1, html-has-lang, valid-lang) and the
    translated home, leaderboards and mission pages (longer strings, other labels) pass axe too."""
    context = browser.new_context(base_url=base_url, locale=language, extra_http_headers={"Accept-Language": language})
    page = context.new_page()
    try:
        found: list[str] = []
        for path in ("/", "/leaderboards/", f"/missions/{world.featured_mission_pk}/"):
            page.goto(path)
            page.wait_for_load_state("networkidle")
            assert (page.get_attribute("html", "lang") or "").lower().replace("_", "-") == language.lower()
            found += axe_violations(page, f"{language} {path}")
        assert not found, "\n".join(sorted(set(found)))
    finally:
        context.close()
