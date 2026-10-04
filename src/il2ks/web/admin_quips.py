"""The admin's Quips page: the rows it shows and the parsing of its one big form (FR-WEB-23; doc 16 "Flavor text").

Form field names: `enabled`; per spot `mode-<spot>`, `hide-<spot>` (the English text of each hidden built-in line, may
repeat), `forget-<spot>` (stale hides to drop); per stored custom quip `c<i>-spot|text|lang|on|del`; per spot a new
quip `new-<spot>-text|lang`. Everything is plain text (the templates escape it); nothing is saved when anything is
invalid (`parse_form` returns the errors)."""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from django.http import QueryDict
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from il2ks.web import flavor, quips
from il2ks.web.quips import CustomQuip, Mode, QuipConfig

_SPACES = re.compile(r"\s+")
_STORED = re.compile(r"^c(\d+)-spot$")


@dataclass(frozen=True, slots=True)
class DefaultRow:
    key: str
    text: str  # in the admin's language
    hidden: bool


@dataclass(frozen=True, slots=True)
class CustomRow:
    index: int
    text: str
    language: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class SpotRow:
    spot: str
    description: str
    mode: Mode
    defaults: tuple[DefaultRow, ...]
    stale_hides: tuple[str, ...]  # hidden texts that match no built-in line any more
    custom: tuple[CustomRow, ...]
    effective: int  # how many lines the spot shows from now on (in the admin's language)


def mode_labels() -> Mapping[Mode, str]:
    return {
        "defaults": _("Built-in quips only"),
        "defaults_and_custom": _("Built-in and my own"),
        "custom_only": _("My own only"),
        "off": _("No quip here"),
    }


def build_rows(config: QuipConfig, language: str) -> list[SpotRow]:
    rows: list[SpotRow] = []
    for spot, variants in flavor.SPOTS.items():
        hidden = config.hidden.get(spot, frozenset[str]())
        keys = quips.default_keys(spot)
        defaults = tuple(DefaultRow(key, str(v), key in hidden) for key, v in zip(keys, variants, strict=True))
        custom = tuple(
            CustomRow(i, q.text, q.language, q.enabled) for i, q in enumerate(config.custom) if q.spot == spot
        )
        rows.append(
            SpotRow(
                spot,
                str(flavor.SPOT_DESCRIPTIONS[spot]),
                config.mode(spot),
                defaults,
                tuple(sorted(hidden - set(keys))),
                custom,
                len(quips.variants(QuipConfig(True, config.modes, config.hidden, config.custom), spot, language)),
            )
        )
    return rows


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def parse_form(post: QueryDict, current: QuipConfig) -> tuple[QuipConfig, list[str]]:
    """The configuration the posted form describes, and the problems that stop it from being saved."""
    errors: list[str] = []
    languages = set(quips.language_codes())
    modes: dict[str, Mode] = {}
    hidden: dict[str, frozenset[str]] = {}
    for spot in flavor.SPOTS:
        mode = post.get(f"mode-{spot}", quips.DEFAULT_MODE)
        if mode not in quips.MODES:
            errors.append(_("Unknown mode for a spot."))
            continue
        modes[spot] = mode
        genuine = set(quips.default_keys(spot))
        keep = {k for k in post.getlist(f"hide-{spot}") if k in genuine}
        stale = current.hidden.get(spot, frozenset[str]()) - genuine - set(post.getlist(f"forget-{spot}"))
        if keep | stale:
            hidden[spot] = frozenset(keep | stale)

    custom: list[CustomQuip] = []

    def add(spot: str, text: str, language: str, enabled: bool) -> None:
        if spot not in flavor.SPOTS or not text:
            return
        if len(text) > quips.MAX_LEN:
            errors.append(
                _("A quip is longer than %(max)d characters: %(start)s...") % {"max": quips.MAX_LEN, "start": text[:30]}
            )
            return
        if language and language not in languages:
            errors.append(_("Unknown language: %(code)s") % {"code": language})
            return
        custom.append(CustomQuip(spot, text, language, enabled))

    indexes = sorted(int(m.group(1)) for key in post if (m := _STORED.match(key)))
    for i in indexes:
        if post.get(f"c{i}-del"):
            continue
        add(
            post.get(f"c{i}-spot", ""),
            _clean(post.get(f"c{i}-text", "")),
            post.get(f"c{i}-lang", ""),
            bool(post.get(f"c{i}-on")),
        )
    for spot in flavor.SPOTS:
        add(spot, _clean(post.get(f"new-{spot}-text", "")), post.get(f"new-{spot}-lang", ""), True)

    for spot in flavor.SPOTS:
        count = sum(1 for q in custom if q.spot == spot)
        if count > quips.MAX_PER_SPOT:
            errors.append(
                ngettext(
                    "At most %(max)d own quip per spot (%(spot)s).",
                    "At most %(max)d own quips per spot (%(spot)s).",
                    quips.MAX_PER_SPOT,
                )
                % {"max": quips.MAX_PER_SPOT, "spot": str(flavor.SPOT_DESCRIPTIONS[spot])}
            )
    if len(custom) > quips.MAX_TOTAL:
        errors.append(_("At most %(max)d own quips in total.") % {"max": quips.MAX_TOTAL})
    return QuipConfig(bool(post.get("enabled")), modes, hidden, tuple(custom)), errors
