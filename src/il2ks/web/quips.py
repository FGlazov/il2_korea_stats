"""Admin-configurable quips (FR-WEB-23, FR-ADM): which flavor lines a site shows.

The choices live on the `SiteSettings` row (`quips_enabled`, `quips` JSON), which every page reads already: a quip costs
no extra query and a save needs no cache invalidation beyond the data-version bump all admin edits do (TD-28). The
stored JSON is parsed tolerantly here (`QuipConfig.from_row`): a hand-edited or outdated row can drop entries, never
break a page.

Per spot a mode decides the pool: `defaults` (the built-in lines, the default), `defaults_and_custom`, `custom_only`,
`off`. A built-in line is hidden by its stable key, its English text (`default_keys`): when a later release rewords it,
the hide matches nothing any more (an "orphan", which the admin page lists). Custom quips are plain text (always
escaped by the templates), at most `MAX_LEN` characters, for one language or (blank) every language. The pick is the
same deterministic hash as `flavor.pick`, over the effective list.

Future seam: scripted custom events (an admin-written condition over a sortie's fields) would add a spot defined by
data instead of by `flavor.SPOTS`/`sortie_spots`; the `modes` / `custom` entries here are already keyed by spot name.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Final, Literal, cast

from django.conf import settings
from django.utils import translation

from il2ks.web import flavor

Mode = Literal["defaults", "defaults_and_custom", "custom_only", "off"]
MODES: Final[tuple[Mode, ...]] = ("defaults", "defaults_and_custom", "custom_only", "off")
DEFAULT_MODE: Final[Mode] = "defaults"
MAX_LEN: Final = 200  # characters of a custom quip
MAX_PER_SPOT: Final = 20  # custom quips per spot
MAX_TOTAL: Final = 300  # custom quips on the whole site (the JSON stays small)


@dataclass(frozen=True, slots=True)
class CustomQuip:
    spot: str
    text: str
    language: str = ""  # '' = every language
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class QuipConfig:
    enabled: bool = True
    modes: Mapping[str, Mode] = field(default_factory=lambda: {})
    hidden: Mapping[str, frozenset[str]] = field(default_factory=lambda: {})
    custom: tuple[CustomQuip, ...] = ()

    def mode(self, spot: str) -> Mode:
        return self.modes.get(spot, DEFAULT_MODE)

    @staticmethod
    def from_row(enabled: bool, raw: object) -> "QuipConfig":
        """The configuration of a `SiteSettings` row; anything malformed or unknown is dropped."""
        data: Mapping[str, object] = cast(Mapping[str, object], raw) if isinstance(raw, dict) else {}
        modes: dict[str, Mode] = {}
        raw_modes: object = data.get("modes")
        if isinstance(raw_modes, dict):
            for spot, mode in cast(Mapping[object, object], raw_modes).items():
                if isinstance(spot, str) and spot in flavor.SPOTS and mode in MODES:
                    modes[spot] = mode
        hidden: dict[str, frozenset[str]] = {}
        raw_hidden: object = data.get("hidden")
        if isinstance(raw_hidden, dict):
            for spot, keys in cast(Mapping[object, object], raw_hidden).items():
                if isinstance(spot, str) and spot in flavor.SPOTS and isinstance(keys, list):
                    texts = frozenset(k for k in cast(list[object], keys) if isinstance(k, str))
                    if texts:
                        hidden[spot] = texts
        custom: list[CustomQuip] = []
        raw_custom: object = data.get("custom")
        if isinstance(raw_custom, list):
            for entry in cast(list[object], raw_custom):
                if not isinstance(entry, dict):
                    continue
                fields = cast(Mapping[str, object], entry)
                spot, text, language = fields.get("spot"), fields.get("text"), fields.get("language", "")
                if not (isinstance(spot, str) and spot in flavor.SPOTS and isinstance(text, str) and text.strip()):
                    continue
                if not isinstance(language, str):
                    continue
                on = fields.get("enabled", True) is not False
                custom.append(CustomQuip(spot, text.strip()[:MAX_LEN], language, on))
        return QuipConfig(enabled, modes, hidden, tuple(custom))

    def to_json(self) -> dict[str, object]:
        """What `SiteSettings.quips` stores: only what differs from the defaults."""
        return {
            "modes": {spot: mode for spot, mode in self.modes.items() if mode != DEFAULT_MODE},
            "hidden": {spot: sorted(keys) for spot, keys in self.hidden.items() if keys},
            "custom": [
                {"spot": q.spot, "text": q.text, "language": q.language, "enabled": q.enabled} for q in self.custom
            ],
        }


@cache
def default_keys(spot: str) -> tuple[str, ...]:
    """The stable keys of a spot's built-in lines, in order: their English text (the msgid)."""
    with translation.override("en"):
        return tuple(str(v) for v in flavor.SPOTS[spot])


def _matches(quip_language: str, language: str) -> bool:
    return quip_language == "" or quip_language == language or quip_language == language.split("-")[0]


def variants(config: QuipConfig, spot: str, language: str) -> list[str]:
    """The effective lines of `spot` in `language`: the visible built-ins (translated into the active language) and
    the enabled custom quips for that language or every language, as the mode allows. Empty = no quip. Raises
    `KeyError` for an unknown spot."""
    defaults = flavor.SPOTS[spot]
    mode = config.mode(spot)
    if not config.enabled or mode == "off":
        return []
    lines: list[str] = []
    if mode in ("defaults", "defaults_and_custom"):
        hidden = config.hidden.get(spot, frozenset[str]())
        keys = default_keys(spot) if hidden else ()
        lines = [str(v) for i, v in enumerate(defaults) if not hidden or keys[i] not in hidden]
    if mode in ("defaults_and_custom", "custom_only"):
        lines += [q.text for q in config.custom if q.spot == spot and q.enabled and _matches(q.language, language)]
    return lines


def pick(config: QuipConfig, spot: str, seed: object, language: str) -> str:
    """The quip for `spot` and `seed`, '' when the effective list is empty. Same hash as `flavor.pick`, so a site with
    no admin choices shows exactly the built-in line of before."""
    pool = variants(config, spot, language)
    if not pool:
        return ""
    digest = hashlib.sha256(f"{spot}:{seed}".encode()).digest()
    return pool[int.from_bytes(digest[:8], "big") % len(pool)]


def language_codes() -> list[str]:
    return [code for code, _name in settings.LANGUAGES]
