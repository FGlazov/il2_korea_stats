"""`web.svg_symbol`: messy icon files still become valid sprite symbols (doc 15, TD-25)."""

import xml.etree.ElementTree as ET

import pytest

from il2ks.web.svg_symbol import SVG_NS, SvgError, symbol_markup

MESSY = {
    "prolog": '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n<!-- Generator: Illustrator -->\n'
    '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h24"/></svg>\n',
    "bom": '﻿<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h24"/></svg>',
    "inkscape": '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
    'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" inkscape:version="1.2" viewBox="0 0 24 24">'
    '<sodipodi:namedview inkscape:zoom="3"/><metadata><x/></metadata>'
    '<g inkscape:label="a"><path d="M0 0h24"/></g></svg>',
    "xlink": '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 24 24">'
    '<defs><path id="p" d="M0 0h24"/></defs><use xlink:href="#p"/><use xlink:href="http://evil.example/x.svg#a"/></svg>',
    "entity": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><text>a&nbsp;b &amp; c &lt;</text></svg>',
    "pixels": '<svg xmlns="http://www.w3.org/2000/svg" width="24px" height="16px"><path d="M0 0h24"/></svg>',
    "no namespace": '<svg viewBox="0 0 24 24"><path d="M0 0h24"/></svg>',
    "script": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><script>alert(1)</script>'
    '<path onclick="x()" d="M0 0"/></svg>',
}


def parsed(markup: str) -> ET.Element:
    return ET.fromstring(f'<svg xmlns="{SVG_NS}">{markup}</svg>')


@pytest.mark.parametrize("name", MESSY)
def test_messy_files_become_valid_xml_inside_the_sprite_root(name: str) -> None:
    symbol = parsed(symbol_markup("a.b", MESSY[name]))[0]

    assert symbol.tag == f"{{{SVG_NS}}}symbol"
    assert symbol.get("id") == "a.b"
    assert symbol.get("viewBox") is not None


def test_foreign_namespaces_metadata_scripts_and_external_links_are_dropped() -> None:
    markup = (
        symbol_markup("a", MESSY["inkscape"]) + symbol_markup("b", MESSY["xlink"]) + symbol_markup("c", MESSY["script"])
    )

    for forbidden in ("inkscape", "sodipodi", "metadata", "script", "onclick", "evil.example", "xlink"):
        assert forbidden not in markup
    assert '<use href="#p"/>' in markup


def test_entities_are_resolved_without_double_escaping() -> None:
    markup = symbol_markup("a", MESSY["entity"])

    assert "<text>a\N{NO-BREAK SPACE}b &amp; c &lt;</text>" in markup
    assert "&amp;amp;" not in markup


def test_pixel_sizes_make_a_numeric_view_box_and_a_root_view_box_wins() -> None:
    assert 'viewBox="0 0 24 16"' in symbol_markup("a", MESSY["pixels"])
    sized = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 5 5"/>'
    assert 'viewBox="0 0 5 5"' in symbol_markup("a", sized)
    percent = '<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="1em"/>'
    assert "viewBox" not in symbol_markup("a", percent)


def test_preserve_aspect_ratio_and_drawing_defaults_stay_on_the_symbol() -> None:
    source = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2 1" preserveAspectRatio="xMinYMid" '
        'stroke="currentColor" id="x" class="y"/>'
    )
    symbol = parsed(symbol_markup("a", source))[0]

    assert symbol.get("preserveAspectRatio") == "xMinYMid"
    assert symbol.get("stroke") == "currentColor"
    assert symbol.get("class") is None


def test_bytes_with_a_bom_are_read() -> None:
    assert "<path" in symbol_markup("a", MESSY["bom"].encode("utf-8"))


@pytest.mark.parametrize(
    "source",
    [
        "",
        "not xml",
        "<svg><path></svg>",
        '<html xmlns="http://www.w3.org/1999/xhtml"/>',
        '<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
        '<svg xmlns="http://www.w3.org/2000/svg">&unknown;</svg>',
    ],
)
def test_unusable_files_raise(source: str) -> None:
    with pytest.raises(SvgError):
        symbol_markup("a", source)
