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
- A population smaller than `MIN_POPULATION` has no thresholds (a percentile of a handful of pilots means nothing).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Literal

type Metric = Literal["survival", "kd", "kl", "air_per_sortie", "air_per_hour", "ground_per_sortie"]
type Band = Literal["top", "high"]

METRICS: Final[tuple[Metric, ...]] = ("survival", "kd", "kl", "air_per_sortie", "air_per_hour", "ground_per_sortie")
MIN_POPULATION: Final = 20  # pilots needed before a distribution is worth showing
SECONDS_PER_HOUR: Final = 3600.0


@dataclass(frozen=True, slots=True)
class MarkRules:
    """The `[marks]` config section."""

    min_sorties: int = 20  # a pilot (and a distribution) needs at least this many counted sorties


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
