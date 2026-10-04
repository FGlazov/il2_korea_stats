"""Achievements / medals (FR-WEB-26, doc 17): a registry of definitions and a pure function over a pilot's sorties.

Each `Achievement` has a `key`, ascending tier `thresholds` and a `progress` function. `progress` reads the pilot's
counted (pilot-role) sorties in chronological order and returns, for every sortie, the best value the pilot has reached
*up to and including* that sortie (a running maximum, so it never falls). `earn` turns that into the tiers: tier `n`
(1-based) is first reached at the first sortie whose value is at least `thresholds[n - 1]`. A medal is never taken back
by a later sortie, and recomputing from the full history always gives the same answer (incremental == rebuild).

Edge cases (doc 17), applied by the progress functions:

- Only pilot sorties are given in; gunner sorties are not counted anywhere.
- A sortie that never left the ground (`not_taken_off`) counts for nothing: it is no sortie flown, adds no week played,
  neither extends nor breaks a streak (the same rule as the ironman streaks, `core.streaks`).
- A death or capture ends a life / survival run. A disconnect or a mission-end cut-off without a death does not.
- Kill counts come from the stored sortie counters, which already exclude friendly kills and wrecks of one's own team.
- Times are UTC (the ISO weeks of `consecutive_weeks`, the hour totals).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

type Unit = Literal["count", "hours", "weeks"]

BADLY_DAMAGED = 0.5
"""`damage_taken` (0 to 1) from which a landed aircraft counts as "badly damaged" (`damaged_landing`)."""


@dataclass(frozen=True, slots=True)
class AchievementSortie:
    """What the achievement rules need to know about one pilot sortie."""

    sortie_id: int
    mission_id: int
    spawned_at: datetime
    ended_at: datetime
    aircraft_id: int
    flight_time_s: float
    kills_air: int
    kills_ground: int
    kills_ground_tank: int
    kills_strike_air: int
    """Bombers and attackers shot down (credited kills of other pilots' aircraft of those classes)."""
    damage_taken: float
    landed: bool
    is_death: bool
    is_captured: bool
    not_taken_off: bool

    @property
    def is_broken(self) -> bool:
        return self.is_death or self.is_captured

    @property
    def counts(self) -> bool:
        """Whether the sortie was flown at all (it left the ground)."""
        return not self.not_taken_off


type Progress = Callable[[Sequence[AchievementSortie]], list[float]]


@dataclass(frozen=True, slots=True)
class Achievement:
    key: str
    thresholds: tuple[int, ...]
    unit: Unit
    progress: Progress

    @property
    def top_tier(self) -> int:
        return len(self.thresholds)


@dataclass(frozen=True, slots=True)
class EarnedTier:
    """Tier `tier` (1-based) of `key` was first reached at `sorties[index]`."""

    key: str
    tier: int
    index: int


# --- progress functions ------------------------------------------------------------------------------------------


def _running_max(values: Sequence[float]) -> list[float]:
    best = 0.0
    out: list[float] = []
    for v in values:
        best = max(best, v)
        out.append(best)
    return out


def _cumulative(per_sortie: Callable[[AchievementSortie], float]) -> Progress:
    def progress(sorties: Sequence[AchievementSortie]) -> list[float]:
        total = 0.0
        out: list[float] = []
        for s in sorties:
            total += per_sortie(s) if s.counts else 0.0
            out.append(total)
        return out

    return progress


def _best_in_one(per_sortie: Callable[[AchievementSortie], float]) -> Progress:
    def progress(sorties: Sequence[AchievementSortie]) -> list[float]:
        return _running_max([per_sortie(s) if s.counts else 0.0 for s in sorties])

    return progress


def life_kills(sorties: Sequence[AchievementSortie]) -> list[float]:
    """Air kills in one life: the kills of the sorties since the last death or capture, including the fatal sortie."""
    current = 0
    values: list[float] = []
    for s in sorties:
        current += s.kills_air
        values.append(float(current))
        if s.is_broken:
            current = 0
    return _running_max(values)


def survived_in_a_row(sorties: Sequence[AchievementSortie]) -> list[float]:
    """Sorties survived in a row (the ironman streak, `core.streaks`)."""
    current = 0
    values: list[float] = []
    for s in sorties:
        if s.is_broken:
            current = 0
        elif s.counts:
            current += 1
        values.append(float(current))
    return _running_max(values)


def week_number(moment: datetime) -> int:
    """A running ISO week number (weeks start on Monday; consecutive weeks differ by 1, across year ends too)."""
    return (moment.toordinal() - 1) // 7


def consecutive_weeks(sorties: Sequence[AchievementSortie]) -> list[float]:
    """The longest run of consecutive ISO weeks (UTC) with at least one sortie flown."""
    last = -10
    run = 0
    values: list[float] = []
    for s in sorties:
        if s.counts:
            week = week_number(s.spawned_at)
            if week == last + 1:
                run += 1
            elif week != last:
                run = 1
            last = max(last, week)
        values.append(float(run))
    return _running_max(values)


def type_veteran(sorties: Sequence[AchievementSortie]) -> list[float]:
    """Flight hours in the one aircraft type the pilot has flown most."""
    per_type: dict[int, float] = {}
    best = 0.0
    values: list[float] = []
    for s in sorties:
        if s.counts:
            hours = per_type.get(s.aircraft_id, 0.0) + s.flight_time_s / 3600
            per_type[s.aircraft_id] = hours
            best = max(best, hours)
        values.append(best)
    return values


def _is_damaged_landing(s: AchievementSortie) -> bool:
    return s.landed and s.damage_taken >= BADLY_DAMAGED and s.kills_air + s.kills_ground > 0


# --- the registry ------------------------------------------------------------------------------------------------

ACHIEVEMENTS: tuple[Achievement, ...] = (
    Achievement("life_kills", (5, 10, 20, 50), "count", life_kills),
    Achievement("sortie_kills", (2, 3, 5, 7), "count", _best_in_one(lambda s: s.kills_air)),
    Achievement("career_kills", (1, 10, 50, 250), "count", _cumulative(lambda s: s.kills_air)),
    Achievement("strike_hunter", (1, 3, 7, 20), "count", _cumulative(lambda s: s.kills_strike_air)),
    Achievement("tank_buster", (3, 10, 25, 100), "count", _cumulative(lambda s: s.kills_ground_tank)),
    Achievement("ground_sortie", (20, 50, 100, 200), "count", _best_in_one(lambda s: s.kills_ground)),
    Achievement("survivor", (5, 10, 25, 50), "count", survived_in_a_row),
    Achievement("damaged_landing", (1, 3, 10), "count", _cumulative(lambda s: 1.0 if _is_damaged_landing(s) else 0.0)),
    Achievement("regular", (2, 4, 8, 16), "weeks", consecutive_weeks),
    Achievement("frequent_flyer", (10, 50, 200, 1000), "count", _cumulative(lambda s: 1.0)),
    Achievement("flight_hours", (1, 10, 50, 200), "hours", _cumulative(lambda s: s.flight_time_s / 3600)),
    Achievement("type_veteran", (2, 10, 30, 100), "hours", type_veteran),
)
BY_KEY: dict[str, Achievement] = {a.key: a for a in ACHIEVEMENTS}


def earn(achievement: Achievement, sorties: Sequence[AchievementSortie]) -> list[EarnedTier]:
    """The tiers of `achievement` the sorties (chronological) reach, each with the first sortie that reached it."""
    values = achievement.progress(sorties)
    earned: list[EarnedTier] = []
    tier = 0
    for index, value in enumerate(values):
        while tier < achievement.top_tier and value >= achievement.thresholds[tier]:
            tier += 1
            earned.append(EarnedTier(achievement.key, tier, index))
    return earned


def earn_all(sorties: Sequence[AchievementSortie]) -> list[EarnedTier]:
    """Every tier of every registered achievement the pilot's sorties (chronological) reach."""
    return [e for achievement in ACHIEVEMENTS for e in earn(achievement, sorties)]
