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

type Unit = Literal["count", "hours", "weeks", "tours", "points", "elo"]
type Kind = Literal["medal", "ribbon"]

ALL_TIME_FACTOR = 5
"""The all-time tiers of a `cumulative` medal are this many times its per-tour thresholds (maintainer, 2026-10-05,
OQ-128: "if it's multi tour then we should increase the thresholds, maybe 5x"). It scales the thresholds in force (the
admin's too), so changing a per-tour tier moves the all-time one with it."""

BADLY_DAMAGED = 0.5
"""`landing_damage` (0 to 1) from which a landing counts as "badly damaged" (`damaged_landing`)."""


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
    """Bombers, attackers and transports shot down (credited kills of other pilots' aircraft of those classes)."""
    landing_damage: float
    """The most damage the aircraft carried into a landing, a repaired one included (doc 13, damage per flight leg)."""
    landed: bool
    is_death: bool
    is_captured: bool
    not_taken_off: bool
    # Facts of the second set (doc 17); defaults keep the first set's callers valid.
    ground_points: float = 0.0
    elo_peak: float = 0.0
    """The highest Elo (prop or jet) held after a win in this sortie; 0 when it had none (`ingest.ratings`)."""
    rams: int = 0
    """Air kills by ramming an enemy aircraft (a part of `kills_air`)."""
    first_blood: bool = False
    """The sortie made the first credited PvP air kill of its mission."""
    multi_kill: int = 0
    """The most air kills within `core.replay.kills.BURST_WINDOW_S` seconds."""
    taxi_accident: bool = False
    strafed_on_ground: bool = False
    friendly_kills: int = 0
    crashed: bool = False
    """The sortie ended with outcome `crashed` after a take-off (not a taxi accident, not strafed on the ground)."""
    ended_by_mission_end: bool = False
    is_attack: bool = False
    """An attack sortie (combat role `attack`): the ground ironman track's; every other sortie is the air track's
    (`core.streaks.track_of`)."""

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
    kind: Kind = "medal"
    """`ribbon`: a simple, common achievement shown as a compact ribbon (doc 17, Display); `medal`: the harder ones."""
    cumulative: bool = False
    """A pure running total of a counter (kills, hours, taxi accidents): a new tour starts it at zero like every
    medal, but the all-time medal counts the *sum* of the tours' totals, so career tiers stay reachable (maintainer,
    2026-10-05, OQ-128 default; `[PROPOSED]`). Every other medal is, all time, the best of the tours (max tier).
    `earn(..., carried_in=n)` replays a tour with the total of the earlier tours already in. `counter` names the
    `PlayerTour` counter holding a tour's total (the all-time tier is found from those without reading the sorties)."""
    counter: str = ""
    shame: bool = False
    """A hall-of-shame entry: shown with the hall of shame, never in the medal row, the ribbon rack or the home feed."""
    all_time_only: bool = False
    """No per-tour meaning (tours in a row): no tour rows, no tour view; its all-time rows come straight from the
    `PlayerTour` rows (`ingest.achievements.rollup_achievements`) and `progress` is unused."""

    @property
    def top_tier(self) -> int:
        return len(self.thresholds)

    @property
    def all_time_thresholds(self) -> tuple[int, ...]:
        """The tiers of the all-time view: `ALL_TIME_FACTOR` times the per-tour ones for a cumulative medal (a career
        spans many tours), the same for every other (the best tour counts)."""
        if not self.cumulative:
            return self.thresholds
        return tuple(ALL_TIME_FACTOR * n for n in self.thresholds)

    def thresholds_for(self, *, all_time: bool) -> tuple[int, ...]:
        return self.all_time_thresholds if all_time else self.thresholds


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
    """A running total of `per_sortie` (sorties that never took off count nothing)."""

    def progress(sorties: Sequence[AchievementSortie]) -> list[float]:
        total = 0.0
        out: list[float] = []
        for s in sorties:
            total += per_sortie(s) if s.counts else 0.0
            out.append(total)
        return out

    return progress


def _total(per_sortie: Callable[[AchievementSortie], float]) -> Progress:
    """Like `_cumulative`, but a sortie that never took off counts too (a taxi accident happens before take-off)."""

    def progress(sorties: Sequence[AchievementSortie]) -> list[float]:
        total = 0.0
        out: list[float] = []
        for s in sorties:
            total += per_sortie(s)
            out.append(total)
        return out

    return progress


def _best_in_one(per_sortie: Callable[[AchievementSortie], float]) -> Progress:
    def progress(sorties: Sequence[AchievementSortie]) -> list[float]:
        return _running_max([per_sortie(s) if s.counts else 0.0 for s in sorties])

    return progress


def life_kills(sorties: Sequence[AchievementSortie]) -> list[float]:
    """Air kills in one air life: the kills of the air-track sorties since the last air-track death or capture,
    including the fatal sortie. Attack sorties neither add to it nor end it (the air ironman track, maintainer
    2026-10-05: losing a ground attacker does not reset the air life)."""
    current = 0
    values: list[float] = []
    for s in sorties:
        if not s.is_attack:
            current += s.kills_air
            if s.is_broken:
                values.append(float(current))
                current = 0
                continue
        values.append(float(current))
    return _running_max(values)


def _survived_on_track(sorties: Sequence[AchievementSortie], *, attack: bool) -> list[float]:
    current = 0
    values: list[float] = []
    for s in sorties:
        if s.is_attack is attack:
            if s.is_broken:
                current = 0
            elif s.counts:
                current += 1
        values.append(float(current))
    return values


def survived_in_a_row(sorties: Sequence[AchievementSortie]) -> list[float]:
    """Sorties survived in a row on the better of the two ironman tracks (`core.streaks`): a death in an attack sortie
    ends only the ground run, one in an air sortie only the air run (maintainer 2026-10-05)."""
    air = _survived_on_track(sorties, attack=False)
    ground = _survived_on_track(sorties, attack=True)
    return _running_max([max(a, g) for a, g in zip(air, ground, strict=True)])


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


def highest_elo(sorties: Sequence[AchievementSortie]) -> list[float]:
    """The highest Elo (prop or jet) reached: the peak after a win, a running maximum over the sorties."""
    return _running_max([s.elo_peak for s in sorties])


def ground_score(sorties: Sequence[AchievementSortie]) -> list[float]:
    """The ground score in total (sortie ground points added up). Penalties can lower it, the medal never drops."""
    total = 0.0
    values: list[float] = []
    for s in sorties:
        total += s.ground_points
        values.append(total)
    return _running_max(values)


def types_flown(sorties: Sequence[AchievementSortie]) -> list[float]:
    """Different aircraft types flown (took off)."""
    seen: set[int] = set()
    values: list[float] = []
    for s in sorties:
        if s.counts:
            seen.add(s.aircraft_id)
        values.append(float(len(seen)))
    return values


def types_with_kills(sorties: Sequence[AchievementSortie]) -> list[float]:
    """Different aircraft types with at least one air kill."""
    seen: set[int] = set()
    values: list[float] = []
    for s in sorties:
        if s.counts and s.kills_air > 0:
            seen.add(s.aircraft_id)
        values.append(float(len(seen)))
    return values


def landings_in_a_row(sorties: Sequence[AchievementSortie]) -> list[float]:
    """Landings in a row. A landed sortie extends the run; a death or capture, or any other sortie that took off and
    did not land, ends it. Like the ironman streak, a sortie that never took off neither extends nor breaks it; a
    sortie the server cut off at mission end without a landing is neutral too (the pilot could not finish it)."""
    current = 0
    values: list[float] = []
    for s in sorties:
        if s.is_broken:
            current = 0
        elif not s.counts:
            pass
        elif s.landed:
            current += 1
        elif not s.ended_by_mission_end:
            current = 0
        values.append(float(current))
    return _running_max(values)


def kills_in_a_day(sorties: Sequence[AchievementSortie]) -> list[float]:
    """The most air kills in one UTC day (the day a sortie spawned on)."""
    per_day: dict[int, int] = {}
    best = 0
    values: list[float] = []
    for s in sorties:
        day = s.spawned_at.toordinal()
        per_day[day] = per_day.get(day, 0) + s.kills_air
        best = max(best, per_day[day])
        values.append(float(best))
    return values


def consecutive_tours(played: Sequence[bool]) -> list[float]:
    """The longest run of consecutive tours flown in, after each tour (`played`: one flag per tour in tour order, True
    = the pilot flew a counted sortie in it; a tour in between that they skipped breaks the run)."""
    run = 0
    values: list[float] = []
    for flew in played:
        run = run + 1 if flew else 0
        values.append(float(run))
    return _running_max(values)


def _never(sorties: Sequence[AchievementSortie]) -> list[float]:
    """The progress of an all-time-only achievement: no per-tour value."""
    return [0.0] * len(sorties)


def _is_damaged_landing(s: AchievementSortie) -> bool:
    return s.landing_damage >= BADLY_DAMAGED and s.kills_air + s.kills_ground > 0


# --- the registry ------------------------------------------------------------------------------------------------

ACHIEVEMENTS: tuple[Achievement, ...] = (
    Achievement("life_kills", (5, 10, 20, 50), "count", life_kills),
    Achievement("sortie_kills", (2, 3, 5, 7), "count", _best_in_one(lambda s: s.kills_air)),
    Achievement(
        "career_kills",
        (1, 10, 50, 250),
        "count",
        _cumulative(lambda s: s.kills_air),
        kind="ribbon",
        cumulative=True,
        counter="kills_air",
    ),
    Achievement("strike_hunter", (1, 3, 7, 20), "count", _cumulative(lambda s: s.kills_strike_air)),
    Achievement(
        "tank_buster",
        (3, 10, 25, 100),
        "count",
        _cumulative(lambda s: s.kills_ground_tank),
        cumulative=True,
        counter="kills_ground_tank",
    ),
    Achievement("ground_sortie", (20, 50, 100, 200), "count", _best_in_one(lambda s: s.kills_ground)),
    Achievement("survivor", (5, 10, 25, 50), "count", survived_in_a_row),
    Achievement("damaged_landing", (1, 3, 10), "count", _cumulative(lambda s: 1.0 if _is_damaged_landing(s) else 0.0)),
    Achievement("regular", (2, 4, 8, 16), "weeks", consecutive_weeks, kind="ribbon"),
    Achievement("frequent_flyer", (10, 50, 200, 1000), "count", _cumulative(lambda s: 1.0), kind="ribbon"),
    Achievement(
        "flight_hours",
        (1, 10, 50, 200),
        "hours",
        _cumulative(lambda s: s.flight_time_s / 3600),
        kind="ribbon",
        cumulative=True,
        counter="flight_time_s",
    ),
    Achievement("type_veteran", (2, 10, 30, 100), "hours", type_veteran),
    # The second set (doc 17, OQ-105).
    Achievement("elo_peak", (1530, 1560, 1600, 1700), "elo", highest_elo),
    Achievement("ground_score", (100, 500, 2000, 5000), "points", ground_score),
    Achievement("ram", (1, 2, 3, 5), "count", _cumulative(lambda s: s.rams)),
    Achievement("first_blood", (1, 3, 5, 10), "count", _cumulative(lambda s: 1.0 if s.first_blood else 0.0)),
    Achievement("multi_kill", (2, 3, 4, 5), "count", _best_in_one(lambda s: s.multi_kill)),
    Achievement("types_flown", (3, 5, 8, 12), "count", types_flown, kind="ribbon"),
    Achievement("types_with_kills", (2, 4, 6, 8), "count", types_with_kills),
    Achievement("landing_streak", (3, 6, 10, 20), "count", landings_in_a_row),
    Achievement("ace_in_a_day", (5, 8, 12, 20), "count", kills_in_a_day),
    # All time only: it reads the tours the pilot flew in, never the sorties (doc 17, OQ-128).
    Achievement("tours_in_a_row", (2, 3, 6, 12), "tours", _never, all_time_only=True),
    # Hall of shame (tongue in cheek, shown with the hall of shame).
    Achievement(
        "shame_taxi",
        (1, 5, 10),
        "count",
        _total(lambda s: 1.0 if s.taxi_accident else 0.0),
        shame=True,
        cumulative=True,
        counter="taxi_accidents",
    ),
    Achievement(
        "shame_friendly",
        (1, 5, 20),
        "count",
        _total(lambda s: s.friendly_kills),
        shame=True,
        cumulative=True,
        counter="friendly_kills",
    ),
    Achievement(
        "shame_strafed",
        (1, 2, 3),
        "count",
        _total(lambda s: 1.0 if s.strafed_on_ground else 0.0),
        shame=True,
        cumulative=True,
        counter="strafed_on_ground",
    ),
    Achievement("shame_crashed", (3, 10, 25), "count", _total(lambda s: 1.0 if s.crashed else 0.0), shame=True),
)
BY_KEY: dict[str, Achievement] = {a.key: a for a in ACHIEVEMENTS}


def reached(
    achievement: Achievement, values: Sequence[float], thresholds: Sequence[int], carried_in: float = 0.0
) -> list[EarnedTier]:
    """The tiers `values` (running, one per step) reach against `thresholds`, each with the first step that did."""
    earned: list[EarnedTier] = []
    tier = 0
    for index, value in enumerate(values):
        while tier < len(thresholds) and value + carried_in >= thresholds[tier]:
            tier += 1
            earned.append(EarnedTier(achievement.key, tier, index))
    return earned


def earn(
    achievement: Achievement,
    sorties: Sequence[AchievementSortie],
    carried_in: float = 0.0,
    *,
    all_time: bool = False,
) -> list[EarnedTier]:
    """The tiers of `achievement` the sorties (chronological) reach, each with the first sortie that reached it.
    `all_time` (a cumulative medal replayed for the all-time view): the tiers are the all-time ones
    (`Achievement.all_time_thresholds`) and `carried_in` the total of the earlier tours, added to every value; the tiers
    already reached by it alone are earned at the first sortie. An all-time-only achievement is never earned from
    sorties (`earn_tours`)."""
    if achievement.all_time_only:
        return []
    return reached(
        achievement, achievement.progress(sorties), achievement.thresholds_for(all_time=all_time), carried_in
    )


def earn_tours(achievement: Achievement, played: Sequence[bool]) -> list[EarnedTier]:
    """The tiers of a tours-in-a-row achievement: `played` has one flag per tour in tour order (the pilot flew in it);
    each tier's `index` is the tour whose run first reached it."""
    return reached(achievement, consecutive_tours(played), achievement.all_time_thresholds)


def earn_all(
    sorties: Sequence[AchievementSortie], *, achievements: Sequence[Achievement] = ACHIEVEMENTS
) -> list[EarnedTier]:
    """Every tier of every achievement in `achievements` (default: the whole registry; the admin's rules pass the
    switched-on ones with their thresholds, `core.achievement_rules`) the pilot's sorties (chronological, of one tour)
    reach. All-time-only achievements have no per-tour tiers."""
    return [e for achievement in achievements for e in earn(achievement, sorties)]
