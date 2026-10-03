"""Pure display helpers for templates: formatting, badge tones, accent colour, sort and page logic.

Only read-time arithmetic and presentation (TD-22). Kept free of template machinery so it is trivially unit-tested;
`web/templatetags/il2ks.py` is the thin Django layer on top. Server owners use the template tags, not this module.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from django.utils.formats import number_format
from django.utils.functional import Promise
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from il2ks.core.catalog.loader import Side, side_of_country

DASH = "—"  # shown instead of a number that cannot be computed

type Tone = Literal["green", "amber", "orange", "red", "blue", "teal", "purple", "grey", "redfor", "blufor"]
type SortFirst = Literal["asc", "desc"]
type Label = str | Promise


def to_float(value: object) -> float | None:
    """Coerce what a template hands a filter (int, float, Decimal, numeric string, None) to a float."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


# --- numbers and times --------------------------------------------------------------------------------------------
def duration(seconds: object) -> str:
    """Seconds as '1 h 23 min', '12 min' or '45 s' (rounded to the second first)."""
    value = to_float(seconds)
    if value is None or value < 0:
        return DASH
    hours, rest = divmod(round(value), 3600)
    minutes, secs = divmod(rest, 60)
    if hours and minutes:
        return _("%(h)d h %(m)d min") % {"h": hours, "m": minutes}
    if hours:
        return _("%(h)d h") % {"h": hours}
    if minutes:
        return _("%(m)d min") % {"m": minutes}
    return _("%(s)d s") % {"s": secs}


def _as_utc(value: datetime) -> datetime:
    """Naive datetimes are taken as UTC (the project stores aware UTC, TIME_ZONE = UTC)."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utc(value: datetime | None) -> str:
    """'2026-09-19 22:34 UTC'. Times are shown in UTC until viewer-local time exists (FR-WEB-17)."""
    return DASH if value is None else _as_utc(value).strftime("%Y-%m-%d %H:%M UTC")


def mission_name(mission_file: object) -> str:
    """'Multiplayer/Dogfight\\Alonzo\\The_Sinuiju_Bridges_1951\\Bridges.msnbin' -> 'Bridges' (a Windows path).

    The scenario's file name without folders and extension, underscores as spaces; the dash when there is none."""
    text = "" if mission_file is None else str(mission_file)
    base = re.split(r"[\\/]", text)[-1].rsplit(".", 1)[0].replace("_", " ").strip()
    return base or DASH


def utc_date(value: datetime | None) -> str:
    """'2026-09-19', the UTC date only."""
    return DASH if value is None else _as_utc(value).strftime("%Y-%m-%d")


def num(value: object, places: int = 0) -> str:
    """Thousands separators (locale aware), with `places` decimals."""
    number = to_float(value)
    if number is None:
        return DASH
    return number_format(round(number, places), decimal_pos=places, use_l10n=True, force_grouping=True)


def ratio(numerator: object, denominator: object, places: int = 2) -> str:
    """numerator / denominator with `places` decimals; the dash when the denominator is 0 (K/D, K/L: TD-22)."""
    top, bottom = to_float(numerator), to_float(denominator)
    if top is None or bottom is None or bottom == 0:
        return DASH
    return num(top / bottom, places)


def per_hour(count: object, seconds: object, places: int = 2) -> str:
    """`count` per flight hour, from a count and flight seconds; the dash without flight time."""
    top, secs = to_float(count), to_float(seconds)
    if top is None or secs is None or secs <= 0:
        return DASH
    return num(top / (secs / 3600), places)


def percent(part: object, whole: object, places: int = 0) -> str:
    """part / whole as a percentage ('87%'); the dash when whole is 0."""
    top, bottom = to_float(part), to_float(whole)
    if top is None or bottom is None or bottom == 0:
        return DASH
    return num(top / bottom * 100, places) + "%"


# --- coalitions ---------------------------------------------------------------------------------------------------
def side_of(value: object) -> Side | None:
    """'redfor'/'blufor' from a country code (5xx/6xx, doc 06) or from the side key itself; None for anything else."""
    if value == "redfor":
        return "redfor"
    if value == "blufor":
        return "blufor"
    code = to_float(value)
    return None if code is None else side_of_country(int(code))


def side_name(value: object, redfor_name: str, blufor_name: str) -> str:
    """The admin-editable display name of a side (FR-ADM-5); 'Neutral' for anything that is neither."""
    side = side_of(value)
    if side == "redfor":
        return redfor_name
    if side == "blufor":
        return blufor_name
    return _("Neutral")


# --- badges -------------------------------------------------------------------------------------------------------
type BadgeSpec = tuple[Label, Tone, str]  # label, tone, icon name under static/il2ks/img/ ('' = a plain dot)

# Keys are the TextChoices values in `il2ks.db.models`. Labels are lazy so they follow the active language (TD-24).
OUTCOMES: Mapping[str, BadgeSpec] = {
    "landed": (gettext_lazy("Landed"), "green", "outcome/landed"),
    "ditched": (gettext_lazy("Ditched"), "amber", "outcome/ditched"),
    "crashed": (gettext_lazy("Crashed"), "orange", "outcome/crashed"),
    "shot_down": (gettext_lazy("Shot down"), "red", "outcome/shot-down"),
    "in_flight": (gettext_lazy("In flight"), "teal", "outcome/in-flight"),
    "not_taken_off": (gettext_lazy("Not taken off"), "grey", "outcome/not-taken-off"),
    "mission_ended": (gettext_lazy("Mission ended"), "grey", "outcome/mission-ended"),
    "unknown": (gettext_lazy("Unknown"), "grey", "outcome/unknown"),
}
FATES: Mapping[str, BadgeSpec] = {
    "in_aircraft": (gettext_lazy("In aircraft"), "grey", ""),
    "bailed_out": (gettext_lazy("Bailed out"), "amber", "outcome/bailed-out"),
    "exited_on_ground": (gettext_lazy("Exited on ground"), "grey", "outcome/exited-on-ground"),
    "mission_ended": (gettext_lazy("Mission ended"), "grey", "outcome/mission-ended"),
    # The flag is also set after landings and bailouts, so it never means "disconnected in flight".
    "disconnected": (gettext_lazy("Left the server"), "purple", "outcome/disconnected"),
    "unknown": (gettext_lazy("Unknown"), "grey", "outcome/unknown"),
}
STATUSES: Mapping[str, BadgeSpec] = {
    "healthy": (gettext_lazy("Healthy"), "green", ""),
    "wounded": (gettext_lazy("Wounded"), "amber", "outcome/wounded"),
    "dead": (gettext_lazy("Dead"), "red", "outcome/dead"),
    "captured": (gettext_lazy("Captured"), "purple", "outcome/captured"),
}
AIRCRAFT_STATUSES: Mapping[str, BadgeSpec] = {  # "destroyed" comes from aircraft_status only, never from damage_taken
    "unharmed": (gettext_lazy("Unharmed"), "green", ""),
    "damaged": (gettext_lazy("Damaged"), "amber", ""),
    "destroyed": (gettext_lazy("Destroyed"), "red", ""),
}
ROLES: Mapping[str, BadgeSpec] = {
    "air_superiority": (gettext_lazy("Air superiority"), "blue", "role/air-superiority"),
    "attack": (gettext_lazy("Attack"), "orange", "role/attack"),
}


def badge_spec(table: Mapping[str, BadgeSpec], value: object) -> tuple[str, Tone, str]:
    """Label, tone and icon for a stored enum value. Unknown or empty values degrade to a grey badge, never an error."""
    key = "" if value is None else str(value)
    found = table.get(key)
    if found is not None:
        label, tone, icon = found
        return str(label), tone, icon
    if not key:
        return DASH, "grey", ""
    return key.replace("_", " ").capitalize(), "grey", ""


# --- accent colour (SiteSettings.accent_color, TD-25) -------------------------------------------------------------
_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


def valid_accent(color: str) -> str | None:
    """The colour lower-cased when it is exactly '#RRGGBB', else None. Only validated values reach the page's CSS."""
    return color.lower() if _HEX_COLOR.fullmatch(color) else None


def _luminance(color: str) -> float:
    """WCAG relative luminance of '#rrggbb'."""
    channels: list[float] = []
    for start in (1, 3, 5):
        value = int(color[start : start + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def accent_css(color: str) -> str:
    """CSS that points the theme's accent variables at `color`; '' when it is not a valid '#RRGGBB'.

    The text colour on accent-filled buttons is white or near-black, whichever contrasts more."""
    valid = valid_accent(color)
    if valid is None:
        return ""
    contrast = "#ffffff" if _luminance(valid) < 0.4 else "#10161c"
    return f":root{{--il2-accent:{valid};--il2-accent-contrast:{contrast}}}"


# --- sorting and paging -------------------------------------------------------------------------------------------
def next_sort(current: str, field: str, first: SortFirst = "asc") -> str:
    """The `?sort=` value a column header link should carry: a click on the active column flips its direction."""
    if current == field:
        return f"-{field}"
    if current == f"-{field}":
        return field
    return field if first == "asc" else f"-{field}"


@dataclass(frozen=True, slots=True)
class PageLink:
    """One pagination slot: a page number, or an ellipsis when `number` is None."""

    number: int | None
    current: bool = False


def page_links(current: int, last: int, around: int = 2) -> list[PageLink]:
    """First and last page, `around` pages each side of the current one, ellipses between the gaps."""
    wanted = sorted({1, last, *range(max(1, current - around), min(last, current + around) + 1)})
    links: list[PageLink] = []
    previous = 0
    for number in wanted:
        if number - previous == 2:  # an ellipsis for a single page would be silly
            links.append(PageLink(previous + 1))
        elif number - previous > 2:
            links.append(PageLink(None))
        links.append(PageLink(number, current=number == current))
        previous = number
    return links
