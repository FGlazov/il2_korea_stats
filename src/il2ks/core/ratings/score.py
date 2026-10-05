"""The air score and the ground score of one sortie (FR-WEB-7, FR-ADM-7, doc 13). A pure function, no database.

Two scores, because air-to-air and ground attack are different skills (maintainer, 2026-10-03):

- **Air score** = points for air kills (a player's aircraft is worth more than an AI one), plus assists.
- **Flight-time points** (optional, off by default; maintainer, 2026-10-05): `flight_time_per_hour` points per hour
  in the air, added to the air score of every pilot sortie, so a quiet intercept patrol is worth something. They are
  kill-like points: the outcome percentages take their share, the flat penalties come off afterwards. Unlike the other
  values these two are not in the `[score]` config section: the site admin sets them (`SiteSettings.score_flight`).
- **Ground score** = points for ground kills, one value per ground-kill category (`GROUND_CATEGORIES`), so trivial
  statics like fences (`other`) are worth very little.
- **Outcome penalties are percentages** (maintainer, 2026-10-03, OQ-63/67): a death costs 80% of the sortie's score, a
  captured pilot 50%, an aircraft lost without a death or capture 20% (`ScoreRules.penalty_*_pct`). The percentage is
  taken from **both** the air and the ground score, only from a positive score (it never makes one negative), and when
  several outcomes apply (a death, a capture, a lost aircraft) only the **largest** one counts.
- **Flat penalties** are subtracted afterwards, from the score of the sortie's combat role (an `attack` sortie pays
  from its ground score, every other sortie from its air score): a suspected early bailout (the pilot left an aircraft
  nobody had touched) and each friendly kill (up to a cap per sortie: a base bombed by mistake isn't a hundred
  offences). A sortie with a flat penalty and no kills has a negative score, and totals can go negative.

Everything is computed from the stored sortie columns, so a changed `[score]` section is applied by `il2ks
rebuild-aggregates` without reprocessing any mission. The values are first guesses to tune with data (product
decisions, no design doc fixes them).
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, cast

from il2ks.core.catalog.loader import GROUND_CATEGORIES, GroundCategory


@dataclass(frozen=True, slots=True)
class ScoreRules:
    """The `[score]` config section: points per event, percentages per outcome. All values are zero or more; flat
    penalties are written positive and subtracted; percentages above 100 count as 100."""

    air_kill_pvp: float = 10.0  # shooting down another player's aircraft
    air_kill_ai: float = 2.0  # shooting down an AI aircraft
    air_assist: float = 3.0  # an assist on a player's or AI aircraft (ground assists score nothing)
    ground_tank: float = 6.0
    ground_vehicle: float = 3.0  # trucks, cars, halftracks, tractors, rocket launchers
    ground_artillery: float = 5.0
    ground_aaa: float = 5.0
    ground_ship: float = 8.0
    ground_train: float = 4.0
    ground_building: float = 2.0
    ground_parked_aircraft: float = 3.0
    ground_other: float = 0.2  # fences, crate and barrel yards, logs, small fuel tanks: trivial
    flight_time_enabled: bool = False  # admin option (not read from the config file): points for time in the air
    flight_time_per_hour: float = 1.0  # admin option: points per hour of flight, less than one AI air kill
    penalty_death_pct: float = 80.0  # percent of the sortie's score lost when the pilot died (0 to 100)
    penalty_capture_pct: float = 50.0  # ... when the pilot was captured
    penalty_plane_lost_pct: float = 20.0  # ... when the aircraft was lost without a death or capture
    penalty_early_bailout: float = 5.0  # suspected early bailout: left an aircraft nobody had hit
    penalty_friendly_kill: float = 3.0  # per friendly kill (statics count: bombing your own base is a mistake too)
    penalty_friendly_kill_cap: float = 5.0  # at most this many friendly kills are penalised per sortie


DEFAULT_SCORE_RULES: Final = ScoreRules()

ADMIN_SCORE_FIELDS: Final = frozenset({"flight_time_enabled", "flight_time_per_hour"})
"""`ScoreRules` fields the site admin sets (`FlightScore`); the `[score]` config section does not read them."""

MAX_FLIGHT_POINTS_PER_HOUR: Final = 100.0


@dataclass(frozen=True, slots=True)
class FlightScore:
    """The admin's flight-time score option: on or off, and the points per hour of flight."""

    enabled: bool = False
    per_hour: float = DEFAULT_SCORE_RULES.flight_time_per_hour

    @staticmethod
    def from_json(raw: object) -> "FlightScore":
        """Tolerant: anything malformed gives the defaults (off), never an error."""
        default = FlightScore()
        if not isinstance(raw, dict):
            return default
        data = cast(Mapping[str, object], raw)
        rate = data.get("per_hour")
        valid = isinstance(rate, int | float) and not isinstance(rate, bool) and math.isfinite(rate)
        per_hour = min(max(float(rate), 0.0), MAX_FLIGHT_POINTS_PER_HOUR) if valid else default.per_hour  # type: ignore[arg-type]
        return FlightScore(data.get("enabled") is True, per_hour)

    def to_json(self) -> dict[str, object]:
        return {"enabled": self.enabled, "per_hour": self.per_hour}


@dataclass(frozen=True, slots=True)
class SortieFacts:
    """What the score reads of one pilot sortie: the stored level-1 columns."""

    attack: bool  # combat role `attack`; anything else pays its penalties from the air score
    kills_air_pvp: int
    kills_air_ai: int
    assists_air: int  # assist credits on air victims; ground assists score nothing
    kills_ground: dict[GroundCategory, int]
    is_death: bool
    is_plane_lost: bool
    is_captured: bool
    suspected_early_bailout: bool
    friendly_kills: int
    flight_time_s: float = 0.0  # time in the air (not taxiing); scores only when the admin switched it on


@dataclass(frozen=True, slots=True)
class SortieScore:
    air: float
    ground: float


def ground_value(rules: ScoreRules, category: GroundCategory) -> float:
    """Points for one ground kill of this category."""
    match category:
        case "tank":
            return rules.ground_tank
        case "vehicle":
            return rules.ground_vehicle
        case "artillery":
            return rules.ground_artillery
        case "aaa":
            return rules.ground_aaa
        case "ship":
            return rules.ground_ship
        case "train":
            return rules.ground_train
        case "building":
            return rules.ground_building
        case "parked_aircraft":
            return rules.ground_parked_aircraft
        case "other":
            return rules.ground_other


def outcome_fraction(rules: ScoreRules, facts: SortieFacts) -> float:
    """The share of the sortie's score lost to how it ended (0 to 1): the largest percentage that applies. A death,
    a capture and a lost aircraft don't add up (a captured pilot always lost the aircraft too)."""
    percent = max(
        rules.penalty_death_pct if facts.is_death else 0.0,
        rules.penalty_capture_pct if facts.is_captured else 0.0,
        rules.penalty_plane_lost_pct if facts.is_plane_lost else 0.0,
    )
    return min(percent, 100.0) / 100.0


def flat_penalty(rules: ScoreRules, facts: SortieFacts) -> float:
    """The points taken off for a suspected early bailout and for friendly kills (a positive number)."""
    return rules.penalty_early_bailout * facts.suspected_early_bailout + rules.penalty_friendly_kill * min(
        facts.friendly_kills, rules.penalty_friendly_kill_cap
    )


def score_sortie(facts: SortieFacts, rules: ScoreRules = DEFAULT_SCORE_RULES) -> SortieScore:
    """Air and ground score of one pilot sortie. Kills count for their own score whatever the combat role. The outcome
    percentage reduces whichever of the two is positive; the flat penalties are charged to the score of the role."""
    air = rules.air_kill_pvp * facts.kills_air_pvp + rules.air_kill_ai * facts.kills_air_ai
    air += rules.air_assist * facts.assists_air
    if rules.flight_time_enabled:
        air += rules.flight_time_per_hour * facts.flight_time_s / 3600.0
    ground = sum(ground_value(rules, c) * facts.kills_ground.get(c, 0) for c in GROUND_CATEGORIES)
    keep = 1.0 - outcome_fraction(rules, facts)
    air, ground = max(air, 0.0) * keep, max(ground, 0.0) * keep
    flat = flat_penalty(rules, facts)
    if facts.attack:
        ground -= flat
    else:
        air -= flat
    return SortieScore(air=round(air, 4), ground=round(ground, 4))
