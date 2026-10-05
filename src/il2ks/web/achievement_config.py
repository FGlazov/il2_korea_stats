"""Admin-configurable achievements (FR-WEB-26, doc 17; the admin's Achievements page, `web.admin_achievements`).

The choices live on the `SiteSettings` row every page reads already (`achievements`, `achievements_applied` JSON), so
they cost no extra query and a save needs only the data-version bump all admin edits do (TD-28). The JSON is parsed
tolerantly (`AchievementConfig.from_row`): a hand-edited or outdated row can drop entries, never break a page.

Two kinds of choice with two effects:

- Words and the on/off switch apply at render time, at once: a switched-off achievement is left out of profiles, sortie
  pages, the overview, the holders page, the home feed and the rarity text. A custom name or description (per
  language; blank = the built-in translated text) is plain text, always escaped by the templates.
- Thresholds, and switching an achievement back on, change which `PlayerAchievement` rows exist. They are computed at
  ingest, so they take effect with a recompute (`ingest.achievements.recompute_with_wanted_rules`, run by `watch`, or
  any `rebuild-aggregates`). Until then the pages show the thresholds the rows were computed with (`applied`), so a
  medal
  never claims a threshold its holder did not meet.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Final, cast

from il2ks.core.achievement_rules import Rules
from il2ks.core.achievements import ACHIEVEMENTS, BY_KEY, Achievement
from il2ks.db.models import SiteSettings

MAX_NAME: Final = 60  # characters of a custom name
MAX_DESCRIPTION: Final = 300  # characters of a custom description

type Texts = Mapping[str, Mapping[str, str]]  # key -> language -> text


def _texts(raw: object, limit: int) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if not isinstance(raw, dict):
        return out
    for key, per_language in cast(Mapping[object, object], raw).items():
        if not (isinstance(key, str) and key in BY_KEY and isinstance(per_language, dict)):
            continue
        for language, text in cast(Mapping[object, object], per_language).items():
            if isinstance(language, str) and language and isinstance(text, str) and text.strip():
                out.setdefault(key, {})[language] = text.strip()[:limit]
    return out


@dataclass(frozen=True, slots=True)
class AchievementConfig:
    wanted: Rules = field(default_factory=Rules)
    """What the admin chose (switches, thresholds)."""
    applied: Rules = field(default_factory=Rules)
    """What the stored rows were computed with."""
    names: Texts = field(default_factory=lambda: {})
    descriptions: Texts = field(default_factory=lambda: {})
    tour_names: Texts = field(default_factory=lambda: {})
    """The per-tour variant's name of a cumulative medal (`names` is its all-time/career one; doc 17, 2026-10-05)."""
    tour_descriptions: Texts = field(default_factory=lambda: {})

    @staticmethod
    def from_row(row: SiteSettings) -> "AchievementConfig":
        data: Mapping[str, object] = row.achievements
        return AchievementConfig(
            Rules.from_json(data),
            Rules.from_json(row.achievements_applied),
            _texts(data.get("names"), MAX_NAME),
            _texts(data.get("descriptions"), MAX_DESCRIPTION),
            _texts(data.get("tour_names"), MAX_NAME),
            _texts(data.get("tour_descriptions"), MAX_DESCRIPTION),
        )

    def to_json(self) -> dict[str, object]:
        """What `SiteSettings.achievements` stores: only what differs from the built-in set."""
        return {
            **self.wanted.to_json(),
            "names": self.names,
            "descriptions": self.descriptions,
            "tour_names": self.tour_names,
            "tour_descriptions": self.tour_descriptions,
        }

    def enabled(self, key: str) -> bool:
        return self.wanted.enabled(key)

    def achievement(self, key: str) -> Achievement | None:
        """The achievement as the pages show it: None when unknown or switched off, else with the thresholds the stored
        rows were computed with."""
        base = BY_KEY.get(key)
        if base is None or not self.wanted.enabled(key):
            return None
        thresholds = self.applied.thresholds.get(key)
        return replace(base, thresholds=thresholds) if thresholds else base

    def active(self) -> list[Achievement]:
        """The switched-on achievements in registry order."""
        return [a for base in ACHIEVEMENTS if (a := self.achievement(base.key)) is not None]

    def name(self, key: str, language: str, *, tour: bool = False) -> str:
        """The custom name; `tour`: that of the per-tour variant of a cumulative medal."""
        return _lookup(self.tour_names if tour else self.names, key, language)

    def description(self, key: str, language: str, *, tour: bool = False) -> str:
        return _lookup(self.tour_descriptions if tour else self.descriptions, key, language)


def _lookup(texts: Texts, key: str, language: str) -> str:
    """The custom text for `key` in `language` (or its base language, `pt` for `pt-br`), '' = use the built-in one."""
    per_language = texts.get(key)
    if not per_language:
        return ""
    return per_language.get(language) or per_language.get(language.split("-")[0]) or ""


DEFAULT_CONFIG: Final = AchievementConfig()
