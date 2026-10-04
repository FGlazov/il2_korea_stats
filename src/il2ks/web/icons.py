"""SVG icons from `static/il2ks/img/` (placeholders now, a designer's files later).

An icon is `<svg class="icon"><use href="<sprite>#event.takeoff"/></svg>`, not an <img>, so it follows the theme
through `currentColor`. The markup is a few dozen bytes, however often a page repeats an icon: all icon files are
combined into one sprite (`sprite()`, served by `web.views.sprite` with a content hash in its URL and a year of
caching). Being one site-wide file, it also serves HTMX swaps, which can bring in icons the page did not have. Lookup
goes through Django's static finders, so a file in `<data dir>/custom/static/il2ks/img/...` replaces a built-in one in
the sprite (TD-25).
"""

import hashlib
import re
from functools import cache
from time import monotonic

from django.conf import settings
from django.contrib.staticfiles import finders
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

IMG_ROOT = "il2ks/img/"
NOT_ICONS = ("pattern/",)  # textures for CSS backgrounds, not icons: kept out of the sprite
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*(/[a-z0-9][a-z0-9_-]*)*")
_CLASS = re.compile(r"[A-Za-z0-9_ -]*")
_SVG = re.compile(r"\s*<svg\b([^>]*)>(.*)</svg>\s*", re.DOTALL)
_ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
# Root attributes that stay on the sprite's <symbol> (the drawing's defaults, inherited by its shapes); the rest of the
# root is the file's own sizing and identity.
_SYMBOL_ATTRS = frozenset(
    {
        "viewBox",
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
_SLUG = re.compile(r"[^a-z0-9]+")


def _read(name: str) -> str | None:
    found = finders.find(f"{IMG_ROOT}{name}.svg")
    path = found if isinstance(found, str) else None
    if path is None:
        return None
    with open(path, encoding="utf-8") as handle:
        return handle.read()


@cache
def _read_cached(name: str) -> str | None:
    return _read(name)


def icon_exists(name: str) -> bool:
    return bool(_NAME.fullmatch(name)) and _source(name) is not None


def _source(name: str) -> str | None:
    return _read(name) if settings.DEBUG else _read_cached(name)


def _symbol_id(name: str) -> str:
    """'event/takeoff' -> 'event.takeoff' (a name never contains a dot, so ids stay unique)."""
    return name.replace("/", ".")


def _symbol(name: str, source: str) -> str:
    """The sprite's <symbol> for one icon file, '' when the file is not a plain <svg>...</svg>."""
    found = _SVG.fullmatch(source)
    if found is None:
        return ""
    attrs = {m[1]: m[2] or m[3] for m in _ATTR.finditer(found[1])}
    if "viewBox" not in attrs and "width" in attrs and "height" in attrs:
        attrs["viewBox"] = f"0 0 {attrs['width']} {attrs['height']}"
    kept = "".join(f' {key}="{escape(value)}"' for key, value in attrs.items() if key in _SYMBOL_ATTRS)
    return f'<symbol id="{_symbol_id(name)}"{kept}>{found[2].strip()}</symbol>'


def _icon_names() -> list[str]:
    """Every icon name (built-in and custom/), sorted, from all static finders."""
    names: set[str] = set()
    for finder in finders.get_finders():
        for relative, _storage in finder.list(None):
            path = relative.replace("\\", "/")
            if path.startswith(IMG_ROOT) and path.endswith(".svg"):
                name = path[len(IMG_ROOT) : -len(".svg")]
                if _NAME.fullmatch(name) and not name.startswith(NOT_ICONS):
                    names.add(name)
    return sorted(names)


def _build_sprite() -> tuple[str, str]:
    symbols = "".join(_symbol(name, source) for name in _icon_names() if (source := _source(name)) is not None)
    sprite = f'<svg xmlns="http://www.w3.org/2000/svg">{symbols}</svg>\n'
    return sprite, hashlib.sha256(sprite.encode()).hexdigest()[:10]


@cache
def _sprite_cached() -> tuple[str, str]:
    return _build_sprite()


_DEBUG_REFRESH_S = 1.0
_debug_built: list[tuple[float, tuple[str, str]]] = []


def sprite() -> tuple[str, str]:
    """(the sprite file's text, a short content hash for its URL). Built once per process; under DEBUG rebuilt at most
    once a second, so an edited icon shows up without a restart but a page's icons do not each rebuild it."""
    if not settings.DEBUG:
        return _sprite_cached()
    now = monotonic()
    if not _debug_built or now - _debug_built[0][0] > _DEBUG_REFRESH_S:
        _debug_built[:] = [(now, _build_sprite())]
    return _debug_built[0][1]


@cache
def _sprite_path() -> str:
    return reverse("web:sprite")


def sprite_url() -> str:
    """The sprite's URL with its content hash, so a changed icon (an override in custom/) is fetched anew."""
    return f"{_sprite_path()}?v={sprite()[1]}"


def icon_markup(name: str, css_class: str = "") -> SafeString:
    """The `<svg><use>` for `name` ('event/takeoff'), '' when the name is invalid or the file is missing.

    The file content is trusted (the repo's or the server owner's `custom/static`); only the name is validated."""
    if not _NAME.fullmatch(name) or not _CLASS.fullmatch(css_class) or _source(name) is None:
        return SafeString("")
    classes = f"icon {css_class}".strip()  # validated above
    href = f"{sprite_url()}#{_symbol_id(name)}"
    return mark_safe(f'<svg class="{classes}" aria-hidden="true"><use href="{href}"/></svg>')


def slug(log_name: str) -> str:
    """'MiG-15bis' -> 'mig-15bis': the file name of an aircraft's own icon (aircraft/<slug>.svg)."""
    return _SLUG.sub("-", log_name.lower()).strip("-")


def aircraft_icon_name(log_name: str, propulsion: str) -> str:
    """The icon to use for an aircraft: its own file if present, else the generic jet or prop, else 'unknown'."""
    own = f"aircraft/{slug(log_name)}" if slug(log_name) else ""
    if own and icon_exists(own):
        return own
    if propulsion == "jet":
        return "aircraft/generic-jet"
    if propulsion == "prop":
        return "aircraft/generic-prop"
    return "aircraft/unknown"
