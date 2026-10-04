"""The icon sprite: icons are `<use>` references to one cached sprite file (doc 15, TD-25, TD-28)."""

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.template import Context, Template
from django.test import Client
from pytest_django.fixtures import Settings

from il2ks.web import icons

pytestmark = pytest.mark.django_db

CUSTOM_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" stroke="currentColor"><path d="M1 1h22"/></svg>'
)


@pytest.fixture(autouse=True)
def fresh_sprite() -> Iterator[None]:
    icons._sprite_cached.cache_clear()  # pyright: ignore[reportPrivateUsage]
    icons._read_cached.cache_clear()  # pyright: ignore[reportPrivateUsage]
    yield
    icons._sprite_cached.cache_clear()  # pyright: ignore[reportPrivateUsage]
    icons._read_cached.cache_clear()  # pyright: ignore[reportPrivateUsage]


def test_icon_tag_is_a_short_reference_not_the_drawing() -> None:
    html = Template('{% load il2ks %}{% icon "event/takeoff" "big" %}').render(Context())
    assert html.startswith('<svg class="icon big" aria-hidden="true"><use href="')
    assert html.endswith('#event.takeoff"/></svg>')
    assert "<path" not in html
    assert len(html) < 160


def test_unknown_icon_renders_nothing() -> None:
    assert icons.icon_markup("event/no-such-icon") == ""
    assert icons.icon_markup("../etc/passwd") == ""


def test_sprite_view_serves_every_icon_once_and_caches_by_hash(client: Client) -> None:
    url = icons.sprite_url()
    response = client.get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/svg+xml"
    assert "immutable" in response["Cache-Control"]
    body = response.content.decode()
    ids = re.findall(r'<symbol id="([^"]+)"', body)
    assert len(ids) == len(set(ids)) > 50
    assert "event.takeoff" in ids
    assert "flag.de" in ids
    assert 'stroke="currentColor"' in body
    assert "no-cache" in client.get("/sprite.svg?v=stale")["Cache-Control"]


def test_custom_static_override_replaces_the_icon_in_the_sprite(tmp_path: Path, settings: Settings) -> None:
    """TD-25: an owner's custom/static/il2ks/img/stat/sorties.svg is what the sprite carries."""
    folder = tmp_path / "il2ks" / "img" / "stat"
    folder.mkdir(parents=True)
    (folder / "sorties.svg").write_text(CUSTOM_ICON, encoding="utf-8")
    (folder / "mine.svg").write_text(CUSTOM_ICON, encoding="utf-8")
    before = icons.sprite()[1]
    settings.STATICFILES_DIRS = [tmp_path, *settings.STATICFILES_DIRS]
    icons._sprite_cached.cache_clear()  # pyright: ignore[reportPrivateUsage]
    icons._read_cached.cache_clear()  # pyright: ignore[reportPrivateUsage]
    text, digest = icons.sprite()
    assert digest != before
    assert '<symbol id="stat.sorties" viewBox="0 0 24 24" stroke="currentColor"><path d="M1 1h22"/></symbol>' in text
    assert 'id="stat.mine"' in text


def test_the_built_sprite_is_valid_xml_even_with_messy_custom_files(
    tmp_path: Path, settings: Settings, client: Client
) -> None:
    """A designer's Inkscape / Illustrator export must not break the sprite; an unusable file counts as missing."""
    from tests.unit.test_svg_symbol import MESSY

    folder = tmp_path / "il2ks" / "img" / "stat"
    folder.mkdir(parents=True)
    for key, text in MESSY.items():
        (folder / f"messy-{key.replace(' ', '-')}.svg").write_bytes(text.encode("utf-8"))
    (folder / "broken.svg").write_text("<svg><path></svg>", encoding="utf-8")
    settings.STATICFILES_DIRS = [tmp_path, *settings.STATICFILES_DIRS]
    icons._sprite_cached.cache_clear()  # pyright: ignore[reportPrivateUsage]
    icons._read_cached.cache_clear()  # pyright: ignore[reportPrivateUsage]

    response = client.get(icons.sprite_url())

    root = ET.fromstring(response.content)  # the whole document, built-in icons included
    ids = {symbol.get("id") for symbol in root}
    assert len(root) == len(ids) > 50
    assert {"stat.messy-prolog", "stat.messy-bom", "stat.messy-xlink", "event.takeoff"} <= ids
    assert "stat.broken" not in ids
    assert not icons.icon_exists("stat/broken")
    assert icons.icon_markup("stat/broken") == ""
    assert icons.icon_exists("stat/messy-bom")
    assert "brand.logo-mark" in ids
    assert "brand.favicon" not in ids
    assert response["Content-Security-Policy"] == "default-src 'none'; style-src 'unsafe-inline'"
