"""Stat marks (FR-WEB-22): where a ratio stands among the pilots, as percentile thresholds. Pure functions, no database.

A mark says "better than N in 10 pilots with at least `MarkRules.min_sorties` sorties". The thresholds are percentiles
of the population, not mean + 2 standard deviations: kill ratios and rates are heavily skewed (a long tail of very
good pilots, a hard floor at 0) and survival is capped at 100%, so "mean + 2 sigma" lands at impossible values or
marks almost nobody; a percentile says directly what share of pilots a value beats, whatever the shape.

Rules:
- A ratio counts for the population only when it is defined (K/D needs a death, per-hour needs flight time).
- Bands, by the value against the stored percentiles: `top` above p90 (the best 10%), `high` above p75 (best 25%),
  nothing else. Strictly above: with ties at a threshold nobody gets a mark they would share with half the field, so
  the claim "better than 9 in 10" stays true. Low values are never marked (nobody is shamed, FR-WEB-22); p10 and p25
  are stored anyway, for the owner's judgement and for later.
- `taxi_per_sortie` and `friendly_kill_rate` are no "better" metrics and never get a badge: the profile's hall of
  shame only picks a gentler quip for a pilot above their p90 (`web.flavor.shame_spot`).
- The score and Elo marks (2026-10-04) have their own populations, the same as the boards they sit next to: scores
  and ratios need `min_sorties` sorties; Elo needs `min_elo_games` encounters in that pool (all time, shown against
  the all-time population on a tour profile too); ground score and tanks per hour need
  `min_time_on_target_s` on target; interception per hour needs `min_air_superiority_s` of air superiority flight. The
  stored `StatThreshold.min_sorties` holds that minimum in the metric's unit (sorties, games, seconds).
- A population smaller than `MIN_POPULATION` has no thresholds (a percentile of a handful of pilots means nothing).
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Literal

type Metric = Literal[
    "survival",
    "kd",
    "kl",
    "air_per_sortie",
    "air_per_hour",
    "ground_per_sortie",
    "taxi_per_sortie",
    "friendly_kill_rate",
    "air_score",
    "ground_score",
    "ground_score_hour",
    "elo_prop",
    "elo_jet",
    "interception_hour",
    "tank_hour",
]
type Band = Literal["top", "high"]

METRICS: Final[tuple[Metric, ...]] = (
    "survival",
    "kd",
    "kl",
    "air_per_sortie",
    "air_per_hour",
    "ground_per_sortie",
    "taxi_per_sortie",
    "friendly_kill_rate",
    "air_score",
    "ground_score",
    "ground_score_hour",
    "elo_prop",
    "elo_jet",
    "interception_hour",
    "tank_hour",
)
ELO_METRICS: Final[tuple[Metric, ...]] = ("elo_prop", "elo_jet")  # all time only: ratings are not per tour
MIN_POPULATION: Final = 20  # pilots needed before a distribution is worth showing
SECONDS_PER_HOUR: Final = 3600.0


@dataclass(frozen=True, slots=True)
class MarkRules:
    """The `[marks]` config section."""

    min_sorties: int = 20  # a pilot (and a distribution) needs at least this many counted sorties
    min_elo_games: int = 5  # Elo marks: encounters in that pool (the `[score]` minimum of the Elo boards)
    min_time_on_target_s: float = 600.0  # ground score / tanks per hour: time on target (the boards' minimum)
    min_air_superiority_s: float = 3600.0  # interception per hour: air superiority flight time (the board's minimum)

    def minimum(self, metric: Metric) -> int:
        """The minimum a pilot needs for `metric`, in its unit (see `unit`), as stored with the thresholds."""
        match unit(metric):
            case "games":
                return max(self.min_elo_games, 1)
            case "seconds":
                return math.ceil(max(self.min_time_on_target_s, 1.0))
            case "flight_seconds":
                return math.ceil(max(self.min_air_superiority_s, 1.0))
            case _:
                return self.min_sorties


DEFAULT_MARK_RULES = MarkRules()


@dataclass(frozen=True, slots=True)
class Totals:
    """The counters the metrics are made of (a `Player` or `PlayerTour` row)."""

    sorties: int
    deaths: int
    planes_lost: int
    kills_air: int  # air kills only: ground kills include fences and crates (OQ-38)
    kills_ground: int
    flight_time_s: float
    taxi_accidents: int = 0
    friendly_kills: int = 0
    score_air: float = 0.0
    score_ground: float = 0.0
    score_ground_attack: float = 0.0  # score earned in attack sorties
    time_on_target_s: float = 0.0
    elo_prop: float = 0.0
    elo_prop_games: int = 0
    elo_jet: float = 0.0
    elo_jet_games: int = 0
    flight_time_air_s: float = 0.0  # flight time of air superiority sorties
    kills_intercept: int = 0  # air kills of bombers and attackers in air superiority sorties
    kills_tank_attack: int = 0  # tanks destroyed in attack sorties


type Unit = Literal["sorties", "games", "seconds", "flight_seconds"]


def unit(metric: Metric) -> Unit:
    """What the pilot's minimum for `metric` counts."""
    match metric:
        case "elo_prop" | "elo_jet":
            return "games"
        case "interception_hour":
            return "flight_seconds"
        case "ground_score_hour" | "tank_hour":
            return "seconds"
        case _:
            return "sorties"


def amount(metric: Metric, totals: Totals) -> float:
    """The pilot's figure the minimum of `metric` is compared with (sorties, encounters in the pool, seconds)."""
    match metric:
        case "elo_prop":
            return totals.elo_prop_games
        case "elo_jet":
            return totals.elo_jet_games
        case "ground_score_hour" | "tank_hour":
            return totals.time_on_target_s
        case "interception_hour":
            return totals.flight_time_air_s
        case _:
            return totals.sorties


def metric_value(metric: Metric, totals: Totals) -> float | None:
    """The metric for these totals, or None where the page shows a dash (denominator 0)."""
    match metric:
        case "survival":
            return _div(max(totals.sorties - totals.deaths, 0), totals.sorties)
        case "kd":
            return _div(totals.kills_air, totals.deaths)
        case "kl":
            return _div(totals.kills_air, totals.planes_lost)
        case "air_per_sortie":
            return _div(totals.kills_air, totals.sorties)
        case "air_per_hour":
            return _div(totals.kills_air * SECONDS_PER_HOUR, totals.flight_time_s)
        case "ground_per_sortie":
            return _div(totals.kills_ground, totals.sorties)
        case "taxi_per_sortie":
            return _div(totals.taxi_accidents, totals.sorties)
        case "friendly_kill_rate":
            return _div(totals.friendly_kills, totals.sorties)
        case "air_score":
            return totals.score_air
        case "ground_score":
            return totals.score_ground
        case "ground_score_hour":
            return _div(totals.score_ground_attack * SECONDS_PER_HOUR, totals.time_on_target_s)
        case "interception_hour":
            return _div(totals.kills_intercept * SECONDS_PER_HOUR, totals.flight_time_air_s)
        case "tank_hour":
            return _div(totals.kills_tank_attack * SECONDS_PER_HOUR, totals.time_on_target_s)
        case "elo_prop":
            return totals.elo_prop if totals.elo_prop_games > 0 else None
        case "elo_jet":
            return totals.elo_jet if totals.elo_jet_games > 0 else None


def _div(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Percentiles of one metric over the population."""

    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    population: int  # pilots with a defined value


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """The q-th percentile (0..100) of an ascending list, by linear interpolation between neighbours (the common
    "type 7" definition, same as numpy's default). One value: that value. Raises ValueError on an empty list."""
    if not sorted_values:
        raise ValueError("percentile of nothing")
    position = (len(sorted_values) - 1) * q / 100.0
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def thresholds(values: Iterable[float | None]) -> Thresholds | None:
    """Thresholds over the defined values, or None when fewer than `MIN_POPULATION` pilots have one."""
    ordered = sorted(v for v in values if v is not None)
    if len(ordered) < MIN_POPULATION:
        return None
    return Thresholds(
        p10=percentile(ordered, 10),
        p25=percentile(ordered, 25),
        p50=percentile(ordered, 50),
        p75=percentile(ordered, 75),
        p90=percentile(ordered, 90),
        population=len(ordered),
    )


def band(value: float | None, limits: Thresholds) -> Band | None:
    """`top` above p90, `high` above p75, else None (including undefined values)."""
    if value is None:
        return None
    if value > limits.p90:
        return "top"
    if value > limits.p75:
        return "high"
    return None
