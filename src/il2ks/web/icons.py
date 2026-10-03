"""Inline SVG icons from `static/il2ks/img/` (placeholders now, a designer's files later).

Icons are inlined, not referenced with <img>, so they follow the theme through `currentColor`. Lookup goes through
Django's static finders, so a file in `<data dir>/custom/static/il2ks/img/...` replaces a built-in one (TD-25).
"""

import re
from functools import cache

from django.conf import settings
from django.contrib.staticfiles import finders
from django.utils.safestring import SafeString, mark_safe

IMG_ROOT = "il2ks/img/"
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*(/[a-z0-9][a-z0-9_-]*)*")
_CLASS = re.compile(r"[A-Za-z0-9_ -]*")
_SVG_OPEN = re.compile(r"<svg\b")
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


def icon_markup(name: str, css_class: str = "") -> SafeString:
    """The inline <svg> for `name` ('event/takeoff'), '' when the name is invalid or the file is missing.

    The file content is trusted (the repo's or the server owner's `custom/static`); only the name is validated."""
    if not _NAME.fullmatch(name) or not _CLASS.fullmatch(css_class):
        return SafeString("")
    source = _source(name)
    if source is None:
        return SafeString("")
    opening = f'<svg class="{f"icon {css_class}".strip()}" aria-hidden="true" focusable="false"'  # class is validated
    return mark_safe(_SVG_OPEN.sub(opening, source.strip(), count=1))


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
