"""Medal names, descriptions and the display rows built from `PlayerAchievement` (FR-WEB-26, doc 17).

The rules and thresholds live in `il2ks.core.achievements`; this module only adds the words (translatable) and shapes
rows for the templates. No database access here: callers pass the rows and the holder counts.

Rarity (doc 17): the share of pilots holding a tier in a scope, from `AchievementHolders` (`Holding`: holders and the
scope's pilots). It is shown as hover text and, below `RARE_BELOW` / `EPIC_BELOW` percent, as a ring / glow on the medal
or ribbon (`[PROPOSED]` thresholds; not on a server with fewer than `MIN_PILOTS_FOR_RARITY` pilots in the scope, where
everything would glow).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from django.urls import reverse
from django.utils.formats import number_format
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext

from il2ks.core.achievements import ACHIEVEMENTS, BY_KEY, Achievement, Kind
from il2ks.db.models import PlayerAchievement
from il2ks.queries.achievements import Holding, HoldingKey
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


RARE_BELOW = 5.0
"""Percent of pilots below which a tier counts as rare (a ring)."""
EPIC_BELOW = 1.0
"""... and as very rare (a glow)."""
COMMON_FEED_FROM = 20.0
"""Percent of pilots from which a bronze-level medal tier is too common for the home feed."""
MIN_PILOTS_FOR_RARITY = 20
"""Fewer pilots in the scope and nobody is "rare": the emphasis would be on every medal."""
RIBBON_STYLES = 4
"""How many stripe patterns the CSS draws (`.ribbon--s0` to `.ribbon--s3`); a ribbon takes `registry index % 4`."""

type RarityLevel = Literal["", "rare", "epic"]


@dataclass(frozen=True, slots=True)
class Rarity:
    """How rare a tier is in a scope: `share` in percent (None = unknown), the emphasis `level` and the hover `text`."""

    share: float | None
    level: RarityLevel
    text: str


NO_RARITY = Rarity(None, "", "")


def rarity(holding: Holding | None) -> Rarity:
    """The rarity of a tier from its holder row. Unknown (no row, or a row from before the pilot counts existed) gives
    `NO_RARITY`: no text, no emphasis."""
    if holding is None or holding.pilots <= 0 or holding.holders <= 0:
        return NO_RARITY
    share = 100 * holding.holders / holding.pilots
    if share < 0.1:
        text = _("Held by less than 0.1%% of pilots") % {}
    else:
        text = _("Held by %(share)s%% of pilots") % {"share": number_format(share, decimal_pos=1 if share < 10 else 0)}
    level: RarityLevel = ""
    if holding.pilots >= MIN_PILOTS_FOR_RARITY:
        level = "epic" if share < EPIC_BELOW else "rare" if share < RARE_BELOW else ""
    return Rarity(share, level, text)


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
    kind: Kind = "medal"
    shame: bool = False


@dataclass(frozen=True, slots=True)
class Medal:
    """A medal or ribbon tier a pilot holds, with the sortie that earned it. `scope` is the tour id (None = all time),
    `style` the ribbon's stripe pattern (see `RIBBON_STYLES`)."""

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
    kind: Kind
    shame: bool
    scope: int | None
    style: int
    rarity: Rarity

    @property
    def href(self) -> str | None:
        """The sortie that earned it, None while its mission is hidden (a hidden mission is never linked)."""
        return None if self.mission_hidden else reverse("web:sortie-detail", args=[self.sortie_id])

    @property
    def tier_range(self) -> range:
        """One step per tier reached, for the ribbon's stripes."""
        return range(self.tier)

    @property
    def hint(self) -> str:
        """The hover text: what it measures, the tier and threshold, the rarity (also read out to screen readers)."""
        parts = [str(self.description), f"{self.tier_name}: {self.threshold}.", self.rarity.text]
        return " ".join(p for p in parts if p)


@dataclass(frozen=True, slots=True)
class MedalSet:
    """What a profile shows, in three groups: medals, the ribbon rack and the hall-of-shame ribbons."""

    medals: list[Medal]
    ribbons: list[Medal]
    shame: list[Medal]

    def __bool__(self) -> bool:
        return bool(self.medals or self.ribbons or self.shame)


def info(achievement: Achievement) -> MedalInfo:
    name, description = TEXTS[achievement.key]
    tiers = tuple(
        Tier(n, tier_name(n), tier_slug(n), threshold_text(achievement, n)) for n in range(1, achievement.top_tier + 1)
    )
    return MedalInfo(
        achievement.key, name, description, icon_name(achievement.key), tiers, achievement.kind, achievement.shame
    )


def all_info() -> list[MedalInfo]:
    return [info(a) for a in ACHIEVEMENTS]


def _style(key: str) -> int:
    return next(n for n, a in enumerate(ACHIEVEMENTS) if a.key == key) % RIBBON_STYLES


def _medal(row: PlayerAchievement, achievement: Achievement, holdings: Mapping[HoldingKey, Holding]) -> Medal:
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
        kind=achievement.kind,
        shame=achievement.shame,
        scope=row.tour_id,
        style=_style(row.key),
        rarity=rarity(holdings.get((row.tour_id, row.key, row.tier))),
    )


def medals_of(
    rows: Iterable[PlayerAchievement], holdings: Mapping[HoldingKey, Holding], *, highest_only: bool
) -> list[Medal]:
    """Display rows in registry order (then scope, all time first, then tier). `highest_only`: just the best tier of
    each achievement in each scope (the profile's rows); rows of achievements no longer registered are skipped."""
    order = {a.key: n for n, a in enumerate(ACHIEVEMENTS)}
    medals = [_medal(r, BY_KEY[r.key], holdings) for r in rows if r.key in BY_KEY]
    medals.sort(key=lambda m: (order[m.key], m.scope is not None, m.scope or 0, m.tier))
    if highest_only:
        best: dict[tuple[str, int | None], Medal] = {}
        for m in medals:
            best[(m.key, m.scope)] = m
        medals = list(best.values())
    return medals


def medal_set(rows: Iterable[PlayerAchievement], holdings: Mapping[HoldingKey, Holding]) -> MedalSet:
    """A profile's best tier of each achievement split into medals, ribbons and hall-of-shame ribbons."""
    best = medals_of(rows, holdings, highest_only=True)
    return MedalSet(
        medals=[m for m in best if m.kind == "medal" and not m.shame],
        ribbons=[m for m in best if m.kind == "ribbon" and not m.shame],
        shame=[m for m in best if m.shame],
    )
