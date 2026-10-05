"""The "History" dropdown in the top navigation looks and aligns like the other top-level links (maintainer 2026-10-05:
"History is not aligned with the rest": its text sat about 4 px higher)."""

from collections.abc import Callable
from typing import Literal

import pytest
from playwright.sync_api import Page

from tests.e2e.helpers import header_search

type SetBranding = Callable[[dict[str, object]], None]

MEASURE = """() => {
  const nav = document.querySelector('.site-nav');
  const out = {};
  const probe = (key, el) => {
    // the text box, not the padded box: a range over the element's contents
    const range = document.createRange();
    range.selectNodeContents(el);
    const text = range.getBoundingClientRect();
    const box = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    out[key] = {
      textTop: text.top, textBottom: text.bottom, boxTop: box.top, boxBottom: box.bottom,
      fontSize: cs.fontSize, fontWeight: cs.fontWeight, fontFamily: cs.fontFamily, letterSpacing: cs.letterSpacing,
      textTransform: cs.textTransform, lineHeight: cs.lineHeight,
    };
  };
  const links = [...nav.querySelectorAll(':scope > ul > li > a')];
  probe('players', links[0]);
  probe('leaderboards', links[1]);
  if (links[2]) probe('custom', links[2]);
  probe('history', nav.querySelector('details.nav-history > summary'));
  return out;
}"""
PROPS = ("fontSize", "fontWeight", "fontFamily", "letterSpacing", "textTransform", "lineHeight")


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("image", [False, True], ids=["plain", "image-header"])
@pytest.mark.parametrize("width", [1280, 360])
def test_history_aligns_with_the_other_nav_links(
    page: Page, set_branding: SetBranding, scheme: Literal["light", "dark"], image: bool, width: int
) -> None:
    set_branding({"links": [["Forum", "https://forum.example/", "forum"]]})
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("/")
    header_search(page).wait_for()
    page.evaluate("document.fonts.ready.then(() => 0)")
    if image:
        page.evaluate("document.querySelector('header.site-header').classList.add('site-header--image')")
    got = page.evaluate(MEASURE)
    history = got["history"]
    for other in ("players", "leaderboards", "custom"):
        ref = got[other]
        for prop in PROPS:
            assert ref[prop] == history[prop], (other, prop, got)
        if abs(ref["boxTop"] - history["boxTop"]) > 10:
            continue  # wrapped onto another row (phone): only the style is comparable
        for key in ("textTop", "textBottom", "boxTop", "boxBottom"):
            assert abs(ref[key] - history[key]) <= 1, (other, key, got)
