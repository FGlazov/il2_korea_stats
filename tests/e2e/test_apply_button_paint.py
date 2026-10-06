"""The filter bars' Apply button is never painted and then hidden (no layout shift, NFR-PERF-6).

The Apply button is the no-JS way to submit a filter bar or the tour selector; with htmx the filters submit on change
and the button is hidden. Hiding it from the deferred `il2ks.js` (the `has-htmx` class) let a slow machine paint the
hero with the button, wrap the tour selector onto two lines and snap back once the script ran: CI, 2026-10-06, a layout
shift of 0.018 on `form.tour-select` and `form.home-search` at 1280 px, on a docs-only commit. The button is now hidden
before first paint by the `js` class that `theme-init.js` sets in the head, and shown again only when htmx turns out
to be missing (`no-htmx`, set by `il2ks.js`).

These tests stall the deferred scripts, so first paint always comes before them, whatever the machine."""

import time

import pytest
from playwright.sync_api import Browser, Page, Route, expect

from tests.e2e.helpers import wait_until_settled
from tests.e2e.test_branding_backgrounds import SHIFT_SOURCES
from tests.e2e.test_frontend_performance import OBSERVERS

DEFERRED_SCRIPTS = ("vendor/htmx.min.js", "il2ks/il2ks.js", "il2ks/localtime.js")
STALL_S = 0.8
PAGES = {"home": "/", "missions": "/missions/"}
WIDTHS = (360, 1280)

# Every animation frame until `load`: was an Apply button visible while scripts are enabled? `offsetParent` is null for
# `display: none` (and for the element's ancestors), so a painted button is one with an offsetParent.
PAINT_WATCH = """
window.__applyPainted = [];
(function watch() {
  document.querySelectorAll('.filter-bar__apply').forEach(function (button) {
    if (button.offsetParent !== null) { window.__applyPainted.push(button.className); }
  });
  if (document.readyState !== 'complete') { requestAnimationFrame(watch); }
})();
"""


def stall(route: Route) -> None:
    """Hold a deferred script back long enough for the browser to paint without it."""
    time.sleep(STALL_S)
    route.continue_()


def is_deferred_script(url: str) -> bool:
    return any(name in url for name in DEFERRED_SCRIPTS)


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("page_name", PAGES)
def test_apply_buttons_are_hidden_before_the_deferred_scripts_run(page: Page, page_name: str, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.route(is_deferred_script, stall)
    page.add_init_script(OBSERVERS)
    page.add_init_script(SHIFT_SOURCES)
    page.add_init_script(PAINT_WATCH)
    page.goto(PAGES[page_name])
    wait_until_settled(page)
    page.wait_for_timeout(300)

    assert page.evaluate("window.__applyPainted") == [], "an Apply button was painted before the scripts hid it"
    in_forms = page.evaluate(
        "window.__shifted.filter(s => /form\\.|\\.hero|\\.filter-bar|\\.tour-select|\\.home-search/.test(s.node))"
    )
    assert in_forms == [], "the filter forms moved after first paint"
    expect(page.locator(".filter-bar__apply").first).to_be_hidden()


@pytest.mark.allow_console_errors  # the blocked htmx request is a console error
def test_without_htmx_the_apply_button_comes_back(page: Page) -> None:
    """JS on, htmx missing (blocked, broken override): the filters cannot submit on change, so the button must show."""
    page.route(lambda url: "vendor/htmx.min.js" in url, lambda route: route.abort())
    page.goto("/missions/")
    page.wait_for_function("document.documentElement.classList.contains('no-htmx')")

    expect(page.locator(".filter-bar__apply").first).to_be_visible()
    assert page.evaluate("document.documentElement.classList.contains('has-htmx')") is False


def test_without_javascript_the_apply_button_shows(
    browser: Browser, base_url: str, browser_context_args: dict[str, object]
) -> None:
    args: dict[str, object] = {**browser_context_args, "java_script_enabled": False}
    context = browser.new_context(**args)  # pyright: ignore[reportArgumentType]
    try:
        no_js = context.new_page()
        no_js.goto(f"{base_url}/missions/")
        expect(no_js.locator(".filter-bar__apply").first).to_be_visible()
        assert no_js.evaluate("document.documentElement.className") == ""
    finally:
        context.close()
