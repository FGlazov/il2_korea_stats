"""One SVG icon file -> one sprite `<symbol>` (`web.icons`), tolerant of what editors and designers save.

Pure Python (no Django), so `il2ks doctor` can check a server owner's custom files with the same code the sprite uses.

The file is parsed as XML (read it as `utf-8-sig`, so a BOM is fine), then re-serialised from the parsed tree, so the
sprite stays one valid XML document whatever a file contains:

- The `<?xml?>` prolog, a DOCTYPE, comments and processing instructions are dropped; named HTML entities such as
  `&nbsp;` become character references (XML only knows five). A file that declares its own entities is refused (no
  entity expansion tricks).
- Only SVG elements stay; `metadata`, `script`, `foreignObject` and every foreign-namespace element (`sodipodi:`,
  `inkscape:`, ...) are dropped with their children. On attributes, foreign-namespace ones and `on*` handlers go;
  `xlink:href` becomes a plain `href`, and only a `#fragment` reference is kept (no external or script links).
- The root's drawing defaults (`viewBox`, `fill`, `stroke`, ..., `preserveAspectRatio`) stay on the `<symbol>`. Without
  a `viewBox` it is made from a numeric `width` and `height` (`24`, `24px`), else left out.
"""

import html.entities
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

NOT_ICONS = (
    "pattern/",
    "brand/favicon",
)  # CSS textures and the tab icon, not `{% icon %}` icons: kept out of the sprite
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
# Root attributes that stay on the sprite's <symbol> (the drawing's defaults, inherited by its shapes); the rest of the
# root is the file's own sizing and identity.
SYMBOL_ATTRS = frozenset(
    {
        "viewBox",
        "preserveAspectRatio",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "fill-rule",
        "clip-rule",
        "opacity",
    }
)
_DROPPED = frozenset({"metadata", "script", "foreignObject"})
_XML_ENTITIES = frozenset({"amp", "lt", "gt", "quot", "apos"})
_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_LENGTH = re.compile(r"\s*([0-9]*\.?[0-9]+)\s*(?:px)?\s*")


class SvgError(ValueError):
    """The file cannot be a sprite symbol; the message says why in plain words."""


def _html_entity(found: re.Match[str]) -> str:
    name = found[1]
    if name in _XML_ENTITIES:
        return found[0]
    codepoint = html.entities.name2codepoint.get(name)
    return found[0] if codepoint is None else f"&#{codepoint};"


def _split(tag: str) -> tuple[str, str]:
    """'{ns}local' -> (ns, local); a name without a namespace has ns ''."""
    if tag.startswith("{"):
        ns, _, local = tag[1:].partition("}")
        return ns, local
    return "", tag


def _attributes(element: ET.Element, *, root: bool) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key, value in element.attrib.items():
        ns, local = _split(key)
        if ns == XLINK_NS and local == "href":
            local, ns = "href", ""
        if ns or local.lower().startswith("on"):
            continue
        if local == "href" and not value.strip().startswith("#"):
            continue
        if root and local not in SYMBOL_ATTRS:
            continue
        out.append((local, value))
    return out


def _children(element: ET.Element, out: list[str]) -> None:
    out.append(escape(element.text or ""))
    for child in element:
        ns, local = _split(child.tag)
        if local and ns in ("", SVG_NS) and local not in _DROPPED:
            attrs = "".join(f" {key}={quoteattr(value)}" for key, value in _attributes(child, root=False))
            inner: list[str] = []
            _children(child, inner)
            content = "".join(inner)
            out.append(f"<{local}{attrs}>{content}</{local}>" if content else f"<{local}{attrs}/>")
        out.append(escape(child.tail or ""))  # the tail belongs to the parent, even when the child is dropped


def _view_box(attrs: dict[str, str]) -> str | None:
    if "viewBox" in attrs:
        return None
    width, height = _LENGTH.fullmatch(attrs.get("width", "")), _LENGTH.fullmatch(attrs.get("height", ""))
    if width is None or height is None:
        return None
    return f"0 0 {width[1]} {height[1]}"


def symbol_markup(symbol_id: str, source: str | bytes) -> str:
    """The `<symbol id=...>...</symbol>` for one icon file's text. Raises `SvgError` when it is not a usable SVG."""
    text = source.decode("utf-8-sig", errors="replace") if isinstance(source, bytes) else source.removeprefix("﻿")
    if re.search(r"<!ENTITY", text, re.IGNORECASE):
        raise SvgError("it declares XML entities, which are not allowed")
    try:
        root = ET.fromstring(_ENTITY.sub(_html_entity, text))
    except ET.ParseError as exc:
        raise SvgError(f"it is not well-formed XML ({exc})") from exc
    ns, local = _split(root.tag)
    if local != "svg" or ns not in ("", SVG_NS):
        raise SvgError("its root element is not <svg>")
    attrs = dict(_attributes(root, root=True))
    box = _view_box({k: v for k, v in root.attrib.items() if "}" not in k})
    if box is not None:
        attrs["viewBox"] = box
    kept = "".join(f" {key}={quoteattr(value)}" for key, value in attrs.items())
    body: list[str] = []
    _children(root, body)
    return f"<symbol id={quoteattr(symbol_id)}{kept}>{''.join(body).strip()}</symbol>"
