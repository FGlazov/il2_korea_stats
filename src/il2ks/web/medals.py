"""Medal names, descriptions and the display rows built from `PlayerAchievement` (FR-WEB-26, doc 17).

The rules and thresholds live in `il2ks.core.achievements`; this module only adds the words (translatable) and shapes
rows for the templates. No database access here: callers pass the rows.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext

from il2ks.core.achievements import ACHIEVEMENTS, BY_KEY, Achievement
from il2ks.db.models import PlayerAchievement
from il2ks.web.display import Label

TIER_NAMES: tuple[Label, ...] = (
    gettext_lazy("Bronze"),
    gettext_lazy("Silver"),
    gettext_lazy("Gold"),
    gettext_lazy("Platinum"),
)
TIER_SLUGS = ("bronze", "silver", "gold", "platinum")

# key -> (name, what it measures). The names are titles; the descriptions say what counts.
TEXTS: Mapping[str, tuple[Label, Label]] = {
    "life_kills": (
        gettext_lazy("Charmed Life"),
        gettext_lazy("Air kills in one life: everything since the last death or capture, the fatal sortie included."),
    ),
    "sortie_kills": (gettext_lazy("Ace of the Sortie"), gettext_lazy("Air kills in a single sortie.")),
    "career_kills": (gettext_lazy("Sky Hunter"), gettext_lazy("Air kills in total, all sorties together.")),
    "strike_hunter": (
        gettext_lazy("Bomber Hunter"),
        gettext_lazy("Enemy bombers and attackers flown by other pilots, shot down."),
    ),
    "tank_buster": (gettext_lazy("Tank Buster"), gettext_lazy("Tanks destroyed in total.")),
    "ground_sortie": (gettext_lazy("Target-Rich"), gettext_lazy("Ground targets destroyed in a single sortie.")),
    "survivor": (
        gettext_lazy("Ironman"),
        gettext_lazy("Sorties survived in a row: no death, no capture. Sorties that never took off are skipped."),
    ),
    "damaged_landing": (
        gettext_lazy("Limping Home"),
        gettext_lazy("Sorties with at least one kill that ended in a landing with the aircraft badly damaged."),
    ),
    "regular": (
        gettext_lazy("Regular"),
        gettext_lazy("Calendar weeks in a row (Monday to Sunday, UTC) with at least one sortie."),
    ),
    "frequent_flyer": (gettext_lazy("Frequent Flyer"), gettext_lazy("Sorties flown: the aircraft took off.")),
    "flight_hours": (gettext_lazy("Hours Aloft"), gettext_lazy("Hours in the air, all sorties together.")),
    "type_veteran": (
        gettext_lazy("Type Veteran"),
        gettext_lazy("Hours in the air in the one aircraft type flown most."),
    ),
}


def tier_name(tier: int) -> Label:
    return TIER_NAMES[min(tier, len(TIER_NAMES)) - 1]


def tier_slug(tier: int) -> str:
    return TIER_SLUGS[min(tier, len(TIER_SLUGS)) - 1]


def icon_name(key: str) -> str:
    return f"medal/{key.replace('_', '-')}"


def threshold_text(achievement: Achievement, tier: int) -> str:
    """What reaching `tier` takes, e.g. '20', '10 h' or '4 weeks'."""
    n = achievement.thresholds[tier - 1]
    if achievement.unit == "hours":
        return _("%(n)s h") % {"n": n}
    if achievement.unit == "weeks":
        return ngettext("%(n)s week", "%(n)s weeks", n) % {"n": n}
    return str(n)


@dataclass(frozen=True, slots=True)
class Tier:
    number: int
    name: Label
    slug: str
    threshold: str


@dataclass(frozen=True, slots=True)
class MedalInfo:
    """One achievement as the overview shows it: its words and its tiers."""

    key: str
    name: Label
    description: Label
    icon: str
    tiers: tuple[Tier, ...]


@dataclass(frozen=True, slots=True)
class Medal:
    """A medal tier a pilot holds, with the sortie that earned it."""

    key: str
    name: Label
    description: Label
    icon: str
    tier: int
    tier_name: Label
    slug: str
    threshold: str
    top_tier: int
    earned_at: datetime
    sortie_id: int
    mission_id: int
    mission_hidden: bool


def info(achievement: Achievement) -> MedalInfo:
    name, description = TEXTS[achievement.key]
    tiers = tuple(
        Tier(n, tier_name(n), tier_slug(n), threshold_text(achievement, n)) for n in range(1, achievement.top_tier + 1)
    )
    return MedalInfo(achievement.key, name, description, icon_name(achievement.key), tiers)


def all_info() -> list[MedalInfo]:
    return [info(a) for a in ACHIEVEMENTS]


def _medal(row: PlayerAchievement, achievement: Achievement) -> Medal:
    name, description = TEXTS[achievement.key]
    return Medal(
        key=row.key,
        name=name,
        description=description,
        icon=icon_name(row.key),
        tier=row.tier,
        tier_name=tier_name(row.tier),
        slug=tier_slug(row.tier),
        threshold=threshold_text(achievement, row.tier),
        top_tier=achievement.top_tier,
        earned_at=row.earned_at,
        sortie_id=row.sortie_id,
        mission_id=row.mission_id,
        mission_hidden=row.mission.is_hidden,
    )


def medals_of(rows: Iterable[PlayerAchievement], *, highest_only: bool) -> list[Medal]:
    """Display rows in registry order (then tier). `highest_only`: just the best tier of each achievement (the profile's
    medal row); rows of achievements no longer registered are skipped."""
    order = {a.key: n for n, a in enumerate(ACHIEVEMENTS)}
    medals = [_medal(r, BY_KEY[r.key]) for r in rows if r.key in BY_KEY]
    medals.sort(key=lambda m: (order[m.key], m.tier))
    if highest_only:
        best: dict[str, Medal] = {}
        for m in medals:
            best[m.key] = m
        medals = list(best.values())
    return medals
