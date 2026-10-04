"""What the browser gets, and how it behaves while loading (NFR-PERF-2, doc 08 "Front-end performance").

Two groups:

- **Budgets** (deterministic, no timing): bytes per resource type, number of requests, nothing from another origin,
  and nothing render-blocking in the head beyond what we chose. Sizes are the decoded bodies the browser received from
  the dev server, so they don't depend on compression or the machine; they only change when we change a page, a
  stylesheet or a script. A page that grows past its budget fails; raise the number on purpose, in the same commit.
  Run with `IL2KS_PERF_REPORT=1 ... -s` to print the measured numbers.
- **Web vitals** (timing, generous limits): LCP, CLS and total blocking time from the Performance API
  (`PerformanceObserver`). CLS is deterministic enough to keep tight; LCP and TBT only catch order-of-magnitude
  regressions (slow CI runners).

Selected with `-m perf` (together with `IL2KS_TEST_E2E=1`) or as part of the e2e job."""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from playwright.sync_api import Page, Request, Response

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
    """Load `url` with a cold cache and wait until the network is quiet (htmx fragments included)."""
    weight = Weight()
    responses: list[Response] = []
    page.on("response", lambda r: responses.append(r))
    page.on("requestfailed", lambda r: weight.failed.append(r.url))
    page.goto(url, wait_until="networkidle")
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


# Budgets: measured on 2026-10-04 on the e2e world, ~25% headroom. Worst page then: 11 requests, 336 KB (the mission
# and sortie pages of the anonymized real log, with 107-122 KB of HTML). The rest is the same on every page and mostly
# vendored: CSS 113 KB (pico 81, site 28), JS 57 KB (htmx 51), fonts 22-44 KB, one image. Uncompressed: production
# serves these gzip/brotli-compressed by WhiteNoise, roughly a quarter of the size.
MAX_REQUESTS = 16
MAX_TOTAL_KB = 420
MAX_HTML_KB = 150
MAX_CSS_KB = 140
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
    assert weight.of("document") <= MAX_HTML_KB * KB, f"HTML {weight.of('document') / KB:.0f} KB (budget {MAX_HTML_KB})"
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


@pytest.mark.parametrize("name", PUBLIC_PAGES)
def test_web_vitals(page: Page, world: World, name: str) -> None:
    page.add_init_script(OBSERVERS)
    page.goto(PUBLIC_PAGES[name](world), wait_until="networkidle")
    page.wait_for_timeout(300)  # layout shifts after the last request (htmx swaps, fonts)
    vitals: dict[str, float] = page.evaluate("window.__vitals")

    if REPORT:
        lcp, cls, tbt = vitals["lcp"], vitals["cls"], vitals["tbt"]
        print(f"VITALS {PUBLIC_PAGES[name](world)}: LCP {lcp:.0f} ms, CLS {cls:.4f}, TBT {tbt:.0f} ms")
    assert vitals["lcp"] <= MAX_LCP_MS, f"LCP {vitals['lcp']:.0f} ms (limit {MAX_LCP_MS})"
    assert vitals["cls"] <= MAX_CLS, f"CLS {vitals['cls']:.3f} (limit {MAX_CLS}): something moves after first paint"
    assert vitals["tbt"] <= MAX_TBT_MS, f"TBT {vitals['tbt']:.0f} ms (limit {MAX_TBT_MS})"
