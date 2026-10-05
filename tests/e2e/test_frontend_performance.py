"""What the browser gets, and how it behaves while loading (NFR-PERF-2, doc 08 "Front-end performance").

Two groups:

- **Budgets** (deterministic, no timing): bytes per resource type, number of requests, nothing from another origin,
  and nothing render-blocking in the head beyond what we chose. Sizes are the decoded bodies the browser received from
  the dev server, so they don't depend on compression or the machine; they only change when we change a page, a
  stylesheet or a script. A page that grows past its budget fails; raise the number on purpose, in the same commit.
  Run with `IL2KS_PERF_REPORT=1 ... -s` to print the measured numbers.
- **Web vitals** (timing, generous limits): LCP, CLS and total blocking time from the Performance API
  (`PerformanceObserver`), each load in a fresh browser context (cold cache). CLS is deterministic enough to keep tight
  and is checked on every load; LCP and TBT only catch order-of-magnitude
  regressions (slow CI runners). Wall-clock numbers jump on a loaded machine (several checks run at once on
  one PC), so a page is loaded up to `VITALS_ATTEMPTS` times and passes LCP / TBT when ONE load is within the limits: a
  real regression is slow every time, a busy CPU only some of the time.

Selected with `-m perf` (together with `IL2KS_TEST_E2E=1`) or as part of the e2e job."""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, Request, Response

from tests.e2e.helpers import wait_until_settled
from tests.e2e.test_smoke import PUBLIC_PAGES
from tests.e2e.world import World

pytestmark = pytest.mark.perf

VENDOR_FONTS = Path(__file__).resolve().parents[2] / "src" / "il2ks" / "web" / "static" / "il2ks" / "vendor"
REPORT = os.environ.get("IL2KS_PERF_REPORT") == "1"
KB = 1024


@dataclass
class Weight:
    """Everything the browser downloaded while loading one page (decoded bytes, by resource type)."""

    requests: int = 0
    bytes_by_type: dict[str, int] = field(default_factory=dict[str, int])
    foreign: list[str] = field(default_factory=list[str])
    failed: list[str] = field(default_factory=list[str])

    def total(self) -> int:
        return sum(self.bytes_by_type.values())

    def of(self, *types: str) -> int:
        return sum(self.bytes_by_type.get(t, 0) for t in types)


def measure(page: Page, url: str, base: str) -> Weight:
    """Load `url` with a cold cache and wait until it has settled (htmx fragments included)."""
    weight = Weight()
    responses: list[Response] = []
    page.on("response", lambda r: responses.append(r))
    page.on("requestfailed", lambda r: weight.failed.append(r.url))
    page.goto(url)
    wait_until_settled(page)
    for response in responses:
        request: Request = response.request
        if not request.url.startswith(("http://", "https://")):
            continue
        weight.requests += 1
        if not request.url.startswith(base):
            weight.foreign.append(request.url)
        try:
            size = len(response.body())
        except Exception:
            size = 0
        kind = request.resource_type
        weight.bytes_by_type[kind] = weight.bytes_by_type.get(kind, 0) + size
    if REPORT:
        parts = ", ".join(f"{k} {v / KB:.1f} KB" for k, v in sorted(weight.bytes_by_type.items()))
        print(f"WEIGHT {url}: {weight.requests} requests, {weight.total() / KB:.1f} KB ({parts})")
    return weight


# Budgets: measured on 2026-10-04 on the e2e world, ~25% headroom. Worst page then: 11 requests, ~310 KB (the mission
# and sortie pages of the anonymized real log). Their HTML was 107-122 KB until the lists were paginated (OQ-96: 10
# missions, 20 sorties, kills and timeline rows a page); then icons became `<use>` references to one
# cached sprite (`web.icons`): now 55-56 KB (from 87-95).
# The sprite is a ~38 KB uncompressed (~9 KB gzipped) request, fetched once and cached for a year (`other` below).
# The rest is the same on every page and mostly
# vendored: CSS 113 KB (pico 81, site 28), JS 57 KB (htmx 51), fonts 22-44 KB, one image. Uncompressed: production
# serves these gzip/brotli-compressed by WhiteNoise, roughly a quarter of the size. This test runs against
# `--dev`, which serves the readable files; production also minifies ours (2026-10-05: own CSS 82 to 59 KB, JS 17 to
# 9.7 KB), so these numbers have ~30 KB extra headroom there.
MAX_REQUESTS = 16
MAX_TOTAL_KB = 390
MAX_HTML_KB = 70
HTML_BUDGET_KB_BY_PAGE = {"sortie from a real log": 90}
"""Maintainer decision (2026-10-04): a detail page gets a higher HTML budget than a list. The sortie page's timeline is
no longer paginated (every event of the sortie is on the page) and the column headers carry descriptions; the real-log
sortie measured 71 KB against the global 70. The global budget stays; only this page may be heavier."""
MAX_SPRITE_KB = 50
MAX_CSS_KB = 160
"""Raised from 140 on 2026-10-05: the e2e world measured 140.5 KB (pico 81, site 53, page sheets 6-7) after the marks
moved into site.css, the Extra-columns button, sticky first columns and the row focus ring. A scan found no duplicated
or dead rules to cut. Uncompressed; production serves it gzip/brotli (~5x smaller) and cached."""
MAX_JS_KB = 75
MAX_FONT_KB = 60
MAX_IMAGE_KB = 20
MAX_STYLESHEETS = 4
"""pico + site + at most two page stylesheets in the head."""
ALLOWED_BLOCKING_SCRIPTS = ("theme-init.js",)
"""Scripts that may block rendering in the head: the theme must be set before first paint (no flash of the wrong
theme). Everything else is `defer` / `async` / at the end of the body."""


@pytest.mark.parametrize("name", PUBLIC_PAGES)
def test_page_weight_budget(page: Page, world: World, base_url: str, name: str) -> None:
    weight = measure(page, PUBLIC_PAGES[name](world), base_url)

    assert not weight.failed, f"requests failed: {weight.failed}"
    assert weight.requests <= MAX_REQUESTS, f"{weight.requests} requests (budget {MAX_REQUESTS})"
    assert weight.total() <= MAX_TOTAL_KB * KB, f"{weight.total() / KB:.0f} KB in total (budget {MAX_TOTAL_KB} KB)"
    html_budget = HTML_BUDGET_KB_BY_PAGE.get(name, MAX_HTML_KB)
    assert weight.of("document") <= html_budget * KB, f"HTML {weight.of('document') / KB:.0f} KB (budget {html_budget})"
    assert weight.of("other") <= MAX_SPRITE_KB * KB, (
        f"icon sprite {weight.of('other') / KB:.0f} KB (budget {MAX_SPRITE_KB})"
    )
    assert weight.of("stylesheet") <= MAX_CSS_KB * KB, f"CSS {weight.of('stylesheet') / KB:.0f} KB"
    assert weight.of("script") <= MAX_JS_KB * KB, f"JS {weight.of('script') / KB:.0f} KB (budget {MAX_JS_KB})"
    assert weight.of("font") <= MAX_FONT_KB * KB, f"fonts {weight.of('font') / KB:.0f} KB (budget {MAX_FONT_KB})"
    assert weight.of("image") <= MAX_IMAGE_KB * KB, f"images {weight.of('image') / KB:.0f} KB (budget {MAX_IMAGE_KB})"


def test_uploaded_fonts_stay_within_the_font_budget(
    page: Page, world: World, base_url: str, set_branding: Callable[[dict[str, object]], None]
) -> None:
    """Admin-uploaded fonts count toward the font bytes. Two typical subset fonts (about 22 KB each; the vendored Barlow
    files stand in) for headings and body fit the budget, and a custom heading font even replaces the shipped one. The
    admin form warns above 150 KB (`web.fonts.RECOMMENDED_FONT_BYTES`); the budgets above describe the shipped look."""
    set_branding(
        {
            "font_files": {
                "heading": str(VENDOR_FONTS / "BarlowCondensed-700.woff2"),
                "body": str(VENDOR_FONTS / "BarlowCondensed-600.woff2"),
            }
        }
    )
    weight = measure(page, PUBLIC_PAGES["home"](world), base_url)

    assert weight.of("font") <= MAX_FONT_KB * KB, f"fonts {weight.of('font') / KB:.0f} KB (budget {MAX_FONT_KB})"
    assert weight.of("font") > 0
    assert weight.foreign == []


@pytest.mark.parametrize("name", PUBLIC_PAGES)
def test_everything_comes_from_our_own_server(page: Page, world: World, base_url: str, name: str) -> None:
    """A self-hosted stats site makes no third-party requests: no CDN, no web fonts, no analytics (privacy, and the
    page works offline on a LAN)."""
    weight = measure(page, PUBLIC_PAGES[name](world), base_url)

    assert weight.foreign == []


@pytest.mark.parametrize("name", PUBLIC_PAGES)
def test_only_the_theme_script_and_the_base_stylesheets_block_rendering(page: Page, world: World, name: str) -> None:
    """Render-blocking resources in the head: stylesheets (needed, but few) and the one tiny theme script."""
    page.goto(PUBLIC_PAGES[name](world))

    blocking_scripts = page.eval_on_selector_all(
        "head script[src]:not([defer]):not([async]):not([type=module])", "els => els.map(e => e.getAttribute('src'))"
    )
    stylesheets = page.eval_on_selector_all(
        "head link[rel=stylesheet]:not([media=print]):not([disabled])", "els => els.map(e => e.getAttribute('href'))"
    )
    inline_blocking = page.eval_on_selector_all("head script:not([src]):not([type=module])", "els => els.length")

    assert all(any(src.split("?")[0].endswith(ok) for ok in ALLOWED_BLOCKING_SCRIPTS) for src in blocking_scripts), (
        f"render-blocking scripts in the head: {blocking_scripts}"
    )
    assert len(stylesheets) <= MAX_STYLESHEETS, f"{len(stylesheets)} blocking stylesheets: {stylesheets}"
    assert inline_blocking == 0, "an inline script in the head blocks rendering"


# --- web vitals -----------------------------------------------------------------------------------------------------

OBSERVERS = """
window.__vitals = {lcp: 0, cls: 0, tbt: 0};
new PerformanceObserver(list => {
  for (const e of list.getEntries()) window.__vitals.lcp = e.startTime;
}).observe({type: 'largest-contentful-paint', buffered: true});
new PerformanceObserver(list => {
  for (const e of list.getEntries()) if (!e.hadRecentInput) window.__vitals.cls += e.value;
}).observe({type: 'layout-shift', buffered: true});
new PerformanceObserver(list => {
  for (const e of list.getEntries()) window.__vitals.tbt += Math.max(0, e.duration - 50);
}).observe({type: 'longtask', buffered: true});
"""

# "Good" Core Web Vitals are LCP <= 2.5 s, CLS <= 0.1, TBT <= 200 ms. The server is local, so these are about our
# own markup, CSS and scripts; the limits leave room for a slow shared CI runner.
MAX_LCP_MS = 2500
MAX_CLS = 0.1
MAX_TBT_MS = 300
VITALS_ATTEMPTS = 3


@pytest.mark.parametrize("name", PUBLIC_PAGES)
def test_web_vitals(browser: Browser, browser_context_args: dict[str, object], world: World, name: str) -> None:
    """Every attempt is a cold load in a fresh browser context (no HTTP cache, so a retry cannot hide a slow first
    load). LCP and TBT pass when one attempt is within the limits (a busy CPU slows some loads); CLS is deterministic
    and must be within the limit in every attempt."""
    problems: list[str] = []
    for _ in range(VITALS_ATTEMPTS):
        context = browser.new_context(**browser_context_args)  # pyright: ignore[reportArgumentType]
        try:
            page = context.new_page()
            page.add_init_script(OBSERVERS)
            page.goto(PUBLIC_PAGES[name](world))
            wait_until_settled(page)
            page.wait_for_timeout(300)  # layout shifts after the last request (htmx swaps, fonts)
            vitals: dict[str, float] = page.evaluate("window.__vitals")
        finally:
            context.close()

        if REPORT:
            lcp, cls, tbt = vitals["lcp"], vitals["cls"], vitals["tbt"]
            print(f"VITALS {PUBLIC_PAGES[name](world)}: LCP {lcp:.0f} ms, CLS {cls:.4f}, TBT {tbt:.0f} ms")
        if vitals["cls"] > MAX_CLS:
            pytest.fail(f"CLS {vitals['cls']:.3f} (limit {MAX_CLS}): something moves after first paint")
        problems = []
        if vitals["lcp"] > MAX_LCP_MS:
            problems.append(f"LCP {vitals['lcp']:.0f} ms (limit {MAX_LCP_MS})")
        if vitals["tbt"] > MAX_TBT_MS:
            problems.append(f"TBT {vitals['tbt']:.0f} ms (limit {MAX_TBT_MS})")
        if not problems:
            return
    pytest.fail(f"over the limits in all {VITALS_ATTEMPTS} cold loads (last one): " + "; ".join(problems))


@pytest.mark.parametrize("name", PUBLIC_PAGES)
def test_every_icon_reference_resolves_in_the_sprite(page: Page, world: World, base_url: str, name: str) -> None:
    """Icons are `<use href="/sprite.svg?v=...#id">`: the sprite loads once (cached for a year) and has every id the
    page uses, so no icon is blank. The reference is the same after an HTMX swap, which needs no symbols of its own."""
    page.goto(PUBLIC_PAGES[name](world))
    wait_until_settled(page)
    result: dict[str, list[str]] = page.evaluate(
        """async () => {
          const refs = [...document.querySelectorAll('svg.icon use')].map(u => u.getAttribute('href'));
          const sprites = new Set(refs.map(r => r.split('#')[0]));
          const missing = [];
          for (const url of sprites) {
            const response = await fetch(url);
            const ids = new Set([...new DOMParser().parseFromString(await response.text(), 'image/svg+xml')
              .querySelectorAll('symbol')].map(s => s.id));
            for (const ref of refs) if (ref.startsWith(url + '#') && !ids.has(ref.split('#')[1])) missing.push(ref);
          }
          return {refs: refs.slice(0, 1), sprites: [...sprites], missing};
        }"""
    )
    assert len(result["sprites"]) <= 1, f"more than one sprite URL: {result['sprites']}"
    assert result["missing"] == []
