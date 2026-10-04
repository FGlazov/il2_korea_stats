"""Admin-defined colours and fonts reach the rendered page in both modes (TD-25)."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest
from playwright.sync_api import Page

type SetBranding = Callable[[dict[str, object]], None]


def computed(page: Page, selector: str, prop: str) -> str:
    return str(page.eval_on_selector(selector, f"el => getComputedStyle(el).{prop}"))


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_colours_follow_the_mode(page: Page, set_branding: SetBranding, scheme: Literal["light", "dark"]) -> None:
    set_branding(
        {
            "theme": {
                "light": {"bg": "#123456", "band": "#654321", "text": "#FEDCBA"},
                "dark": {"bg": "#0A0B0C", "band": "#0C0B0A", "text": "#ABCDEF"},
            }
        }
    )
    page.emulate_media(color_scheme=scheme)
    page.goto("/")
    if scheme == "light":
        assert computed(page, "html", "backgroundColor") == "rgb(18, 52, 86)"
        assert computed(page, "header.site-header", "backgroundColor") == "rgb(101, 67, 33)"
        assert computed(page, "main", "color") == "rgb(254, 220, 186)"
    else:
        assert computed(page, "html", "backgroundColor") == "rgb(10, 11, 12)"
        assert computed(page, "header.site-header", "backgroundColor") == "rgb(12, 11, 10)"
        assert computed(page, "main", "color") == "rgb(171, 205, 239)"


def test_the_theme_toggle_switches_between_the_two_sets(page: Page, set_branding: SetBranding) -> None:
    set_branding({"theme": {"light": {"bg": "#123456"}, "dark": {"bg": "#0A0B0C"}}})
    page.emulate_media(color_scheme="light")
    page.goto("/")
    assert computed(page, "html", "backgroundColor") == "rgb(18, 52, 86)"
    page.get_by_role("button", name="Switch between light and dark").click()
    assert computed(page, "html", "backgroundColor") == "rgb(10, 11, 12)"


def test_fonts_are_applied(page: Page, set_branding: SetBranding) -> None:
    set_branding({"heading_font": "mono", "body_font": "serif"})
    page.goto("/")
    assert "monospace" in computed(page, "h1", "fontFamily") or "Mono" in computed(page, "h1", "fontFamily")
    assert "Georgia" in computed(page, "main", "fontFamily")


VENDOR = Path(__file__).resolve().parents[2] / "src" / "il2ks" / "web" / "static" / "il2ks" / "vendor"


def test_uploaded_fonts_are_applied_and_loaded(page: Page, set_branding: SetBranding) -> None:
    """A custom heading and body font: the computed family is the generated one and the browser really loaded the
    files from our own server (a font file the browser rejects would stay `unloaded` or `error`)."""
    set_branding(
        {
            "font_files": {
                "heading": str(VENDOR / "BarlowCondensed-700.woff2"),
                "body": str(VENDOR / "BarlowCondensed-600.woff2"),
            }
        }
    )
    page.goto("/", wait_until="networkidle")

    assert "il2-font-" in computed(page, "h1", "fontFamily")
    assert "il2-font-" in computed(page, "main", "fontFamily")
    states: list[str] = page.evaluate(
        "Array.from(document.fonts).filter(f => f.family.includes('il2-font-')).map(f => f.status)"
    )
    assert states == ["loaded", "loaded"]
