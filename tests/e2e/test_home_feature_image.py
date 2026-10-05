"""The large front-page image (FR-ADM-2): dominant, right below the hero (maintainer 2026-10-05: search and tour
selection above the image), scales on phones, light and dark, picks up a changed source file by polling within seconds,
and with it off the home page is as before.

Screenshots go to `IL2KS_FEATURE_SHOTS` (default `<tmp>/il2ks-feature-image`; never committed).
"""

import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image, ImageDraw
from playwright.sync_api import Page

type SetBranding = Callable[[dict[str, object]], None]

SHOTS = Path(os.environ.get("IL2KS_FEATURE_SHOTS", Path(tempfile.gettempdir()) / "il2ks-feature-image"))
POLL_DEADLINE_S = 20.0


def make_map(path: Path, colour: str) -> None:
    """A 3200 x 1800 'map': land colour, a river, front lines and some labels (what a real situation map looks like)."""
    image = Image.new("RGB", (3200, 1800), colour)
    draw = ImageDraw.Draw(image)
    draw.line([(0, 400), (900, 700), (1800, 600), (3200, 1300)], fill="#2d6fb5", width=60)
    draw.line([(1200, 0), (1500, 900), (1300, 1800)], fill="#c0392b", width=14)
    draw.line([(1700, 0), (1900, 900), (1700, 1800)], fill="#2c3e9e", width=14)
    for x in range(200, 3200, 400):
        for y in range(150, 1800, 300):
            draw.ellipse([x, y, x + 24, y + 24], fill="#222222")
    draw.text((1400, 860), "FRONT LINE", fill="#000000")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


@pytest.fixture
def map_file(tmp_path: Path) -> Path:
    path = tmp_path / "maps" / "situation.png"
    make_map(path, "#8fb573")
    return path


FEATURE = {"alt": "Map of the current front line", "caption": "Situation at 14:00"}


def test_off_by_default_the_home_page_starts_with_the_hero(page: Page) -> None:
    page.goto("/")
    assert page.locator(".home-feature").count() == 0
    assert page.locator("main > :first-child.hero, main section.hero").count() >= 1


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize(("name", "width", "height"), [("desktop", 1280, 900), ("phone", 390, 800)])
def test_the_image_dominates_the_front_page(
    page: Page,
    set_branding: SetBranding,
    map_file: Path,
    scheme: Literal["light", "dark"],
    name: str,
    width: int,
    height: int,
) -> None:
    set_branding({"feature_image": {"path": str(map_file), **FEATURE}})
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": height})
    page.goto("/")
    img = page.locator(".home-feature__img")
    img.wait_for()
    page.wait_for_function("document.querySelector('.home-feature__img').complete")
    page.evaluate("document.fonts.ready.then(() => 0)")

    box = img.bounding_box()
    hero = page.locator("section.hero").bounding_box()
    main = page.locator("main").bounding_box()
    assert box is not None
    assert hero is not None
    assert main is not None
    assert hero["y"] + hero["height"] <= box["y"], "the hero (search, tour selection) comes before the image"
    assert box["width"] >= min(main["width"] * 0.85, 1000), "the image spans the container"
    assert box["height"] <= height * 0.76, "never taller than most of a screen"
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
    assert page.evaluate("document.querySelector('.home-feature__img').naturalWidth") > 0
    assert page.get_by_role("img", name=FEATURE["alt"]).count() == 1
    assert page.get_by_text(FEATURE["caption"]).is_visible()
    assert page.locator(".home-feature time[data-il2-time]").count() == 1
    link = page.locator("a.home-feature__link")
    assert (link.get_attribute("href") or "").startswith("/media/branding/feature-")
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"home-{name}-{scheme}.png"), full_page=False)
    if name == "phone":  # the phone variant (960 px) is what a narrow screen downloads
        assert "-s.webp" in str(page.evaluate("document.querySelector('.home-feature__img').currentSrc"))


def test_a_changed_file_is_picked_up_by_polling(page: Page, set_branding: SetBranding, map_file: Path) -> None:
    set_branding({"feature_image": {"path": str(map_file), **FEATURE}})
    page.goto("/")
    first = page.locator(".home-feature__img").get_attribute("src")
    assert first
    make_map(map_file, "#d9c27a")  # another tool regenerates the map
    deadline = time.monotonic() + POLL_DEADLINE_S
    seen = first
    while time.monotonic() < deadline and seen == first:
        time.sleep(1.0)
        page.goto("/")
        seen = page.locator(".home-feature__img").get_attribute("src") or ""
    assert seen != first, f"the new map did not appear within {POLL_DEADLINE_S:.0f} s"
