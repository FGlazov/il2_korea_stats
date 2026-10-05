"""Accessibility (doc 16 "Accessibility", target WCAG 2.1 AA for the public pages): axe-core on every public page,
in light and dark, at phone and desktop width, with the `<details>` menus (columns, language) opened too.

axe-core comes from `axe-playwright-python` (a dev-group dependency that bundles `axe.min.js`): the test injects it,
the site never ships it and nothing loads from a CDN. Run only this file with
`IL2KS_TEST_E2E=1 uv run pytest tests/e2e/test_accessibility.py -m e2e`. A failure lists the rule, the impact and
the offending elements. Rules that cannot be fixed without a product decision go in `ALLOWED` with the reason.
"""

from typing import Any, cast

import pytest
from axe_playwright_python.sync_playwright import Axe  # pyright: ignore[reportMissingTypeStubs]
from playwright.sync_api import Browser, Page

from tests.e2e.test_visual_qa import OPEN_DETAILS_JS, PAGES, with_theme
from tests.e2e.world import World

WIDTHS = (360, 1280)
SCHEMES = ("light", "dark")
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]

ALLOWED: dict[str, str] = {}
"""axe rule id -> why the site may break it. Empty: nothing is waived."""

PUBLIC = [name for name in PAGES if name != "styleguide"]
"""The debug-only style guide is not a public page."""


def axe_violations(page: Page, label: str) -> list[str]:
    """axe-playwright-python ships no type information: the untyped call is cast to the JSON shape axe documents."""
    options = {"runOnly": {"type": "tag", "values": TAGS}, "resultTypes": ["violations"]}
    results = Axe().run(page, options=options)  # pyright: ignore[reportUnknownMemberType]
    found = cast(list[dict[str, Any]], results.response["violations"])  # pyright: ignore[reportUnknownMemberType]
    lines: list[str] = []
    for violation in found:
        if violation["id"] in ALLOWED:
            continue
        targets = "; ".join(" ".join(str(t) for t in node["target"]) for node in violation["nodes"][:5])
        lines.append(f"[{label}] {violation['id']} ({violation['impact']}, {len(violation['nodes'])}x): {targets}")
    return lines


@pytest.mark.parametrize("name", PUBLIC)
def test_public_page_has_no_axe_violations(page: Page, world: World, name: str) -> None:
    """axe-core finds nothing (WCAG 2.1 A/AA + best practices) on `name`: 2 schemes x 2 widths, menus open too."""
    url = PAGES[name](page, world)
    found: list[str] = []
    for scheme in SCHEMES:
        page.emulate_media(color_scheme=scheme)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(with_theme(url, scheme))
            page.wait_for_load_state("networkidle")
            tag = f"{name} {width}px {scheme}"
            found += axe_violations(page, tag)
            if page.locator("details").count():
                page.evaluate(OPEN_DETAILS_JS)
                page.wait_for_timeout(450)  # Pico fades a dropdown in
                found += axe_violations(page, tag + " open")
    assert not found, f"{url}\n" + "\n".join(sorted(set(found)))


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
        assert not found, "\n".join(found)
    finally:
        context.close()
