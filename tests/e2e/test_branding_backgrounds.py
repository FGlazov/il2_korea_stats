"""Admin-uploaded background pictures for the header and the home banner (FR-ADM-2), in a real browser.

Both pictures are the worst case for contrast on purpose (plain white; a busy black-and-white stripe pattern), set at
the strongest darkening the admin may choose (80 %) and at the default. For the home page and an inner page, at 360 and
1280 px, in light and dark mode:

- the visual-QA layout audit finds nothing (no horizontal scroll, no clipped text, no overlaps);
- axe finds no violations (axe cannot judge text on a picture, so contrast is measured separately, below);
- text contrast is measured on the rendered pixels behind each piece of text (text made transparent, element
  screenshotted, the brightest background pixel against the text colour: 4.5:1 normal, 3:1 large);
- layout shift stays at 0 (a picture only paints; it never moves a box) and the header and hero keep their height;
- without a picture the markup is the shipped one.

Screenshots go to `IL2KS_QA_SHOTS` like the visual QA."""

import io
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image, ImageDraw
from playwright.sync_api import Page

from tests.e2e.helpers import wait_until_settled
from tests.e2e.test_accessibility import axe_violations
from tests.e2e.test_frontend_performance import MAX_CLS, OBSERVERS
from tests.e2e.test_visual_qa import audit_page, shots_dir, with_theme

type SetBranding = Callable[[dict[str, object]], None]

WIDTHS = (360, 1280)
SCHEMES = ("light", "dark")
PAGES = {"home": "/", "inner": "/aircraft/"}


def luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: float, b: float) -> float:
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


@pytest.fixture
def pictures(tmp_path: Path) -> dict[str, Path]:
    white = tmp_path / "white.png"
    Image.new("RGB", (1920, 500), "white").save(white)
    busy = tmp_path / "busy.png"
    image = Image.new("RGB", (1920, 500), "white")
    draw = ImageDraw.Draw(image)
    for x in range(0, 1920, 24):
        draw.rectangle([x, 0, x + 11, 500], fill="black")
    for y in range(0, 500, 40):
        draw.rectangle([0, y, 1920, y + 6], fill="#ffffff")
    image.save(busy)
    return {"white": white, "busy": busy}


def both(picture: Path, shade: int, position: str = "right") -> dict[str, object]:
    spec = {"file": str(picture), "position": position, "shade": shade}
    return {"home_bg": spec, "header_bg": spec}


BOXES_JS = (
    "['header.site-header', '.hero'].map(s => {"
    " const r = document.querySelector(s).getBoundingClientRect(); return [r.width, r.height]; })"
)
SHIFT_SOURCES = """
window.__shifted = [];
new PerformanceObserver(list => {
  for (const e of list.getEntries()) {
    if (e.hadRecentInput) continue;
    for (const src of e.sources || []) {
      const node = src.node && src.node.nodeType === 1 ? src.node : src.node && src.node.parentElement;
      window.__shifted.push({
        value: e.value,
        node: node ? node.tagName.toLowerCase() + "." + String(node.className).split(" ").join(".") : "?",
        inPictureArea: !!(node && node.closest("header.site-header, .hero")),
      });
    }
  }
}).observe({type: "layout-shift", buffered: true});
"""
TEXT_TARGETS = ("header .brand__title", "header .site-nav a", ".hero h1", ".hero .lead")


def contrasts(page: Page) -> dict[str, float]:
    """The lowest contrast between each target's text colour and the rendered background behind it (visible ones)."""
    page.evaluate("document.activeElement instanceof HTMLElement && document.activeElement.blur()")
    page.mouse.move(1, 890)  # nothing is hovered, and the hover/focus transitions have time to finish
    page.wait_for_timeout(500)
    colours: dict[str, list[int]] = {}
    for selector in TEXT_TARGETS:
        element = page.locator(selector).first
        if element.count() and element.is_visible():
            colours[selector] = element.evaluate(
                "el => getComputedStyle(el).color.match(/[0-9.]+/g).slice(0, 3).map(Number)"
            )
    page.add_style_tag(content="header *, .hero * { color: transparent !important; text-shadow: none !important; }")
    result: dict[str, float] = {}
    for selector, colour in colours.items():
        image = Image.open(io.BytesIO(page.locator(selector).first.screenshot(animations="disabled"))).convert("RGB")
        brightest = max(luminance(px) for px in image.get_flattened_data())  # pyright: ignore[reportArgumentType]
        result[selector] = ratio(luminance((colour[0], colour[1], colour[2])), brightest)
    return result


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("page_name", PAGES)
@pytest.mark.parametrize("picture", ["white", "busy"])
def test_pictures_keep_layout_text_and_axe_clean(
    page: Page,
    set_branding: SetBranding,
    pictures: dict[str, Path],
    picture: str,
    page_name: str,
    width: int,
    scheme: Literal["light", "dark"],
) -> None:
    set_branding(both(pictures[picture], 80))
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(with_theme(PAGES[page_name], scheme))
    wait_until_settled(page)
    assert page.locator("header.site-header--image").count() == 1
    assert (page.locator(".hero--image").count() == 1) == (page_name == "home")

    problems = audit_page(
        page, f"{picture}-{page_name}-{width}-{scheme}", with_theme(PAGES[page_name], scheme), shots_dir()
    )
    # audit_page leaves the page at its last width and scheme with a menu open (the History dropdown would sit over
    # the hero heading): measure on a fresh load in this case's own state.
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(with_theme(PAGES[page_name], scheme))
    wait_until_settled(page)
    problems += axe_violations(page, f"{picture} {page_name} {width} {scheme}")
    for selector, found in contrasts(page).items():
        needed = 3.0 if selector == ".hero h1" else 4.5
        if found < needed:
            problems.append(f"{selector}: contrast {found:.2f}:1 over the picture (needs {needed})")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("width", WIDTHS)
def test_a_picture_moves_nothing(page: Page, set_branding: SetBranding, pictures: dict[str, Path], width: int) -> None:
    """Same box sizes with and without the pictures, and no layout shift while it loads."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("/")
    wait_until_settled(page)
    before = page.evaluate(BOXES_JS)

    set_branding(both(pictures["busy"], 60))
    page.add_init_script(OBSERVERS)
    page.add_init_script(SHIFT_SOURCES)
    page.goto("/")
    wait_until_settled(page)
    page.wait_for_timeout(300)

    after = page.evaluate(BOXES_JS)
    assert after == before
    # The pictures must move nothing in the header or the hero. Elsewhere a slow CI runner can record a tiny shift while
    # the rest of the page loads (CLS 0.011 once, unrelated to the pictures): that part gets the site-wide budget.
    assert page.evaluate("window.__shifted.filter(s => s.inPictureArea)") == []
    assert page.evaluate("window.__vitals.cls") <= MAX_CLS, page.evaluate("window.__shifted")
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_without_pictures_the_markup_is_the_shipped_one(page: Page, set_branding: SetBranding) -> None:
    set_branding({})
    page.goto("/")

    assert page.locator(".site-header--image, .hero--image").count() == 0
    assert page.locator(".hero__mark").count() == 1
    assert page.evaluate("getComputedStyle(document.querySelector('.hero')).backgroundImage").startswith("url(")  # camo
    assert page.locator("link[rel=icon]").get_attribute("href", timeout=1000).endswith("favicon.svg")  # type: ignore[union-attr]
