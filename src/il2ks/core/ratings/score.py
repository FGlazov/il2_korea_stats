"""The air score and the ground score of one sortie (FR-WEB-7, FR-ADM-7, doc 13). A pure function, no database.

Two scores, because air-to-air and ground attack are different skills (maintainer, 2026-10-03):

- **Air score** = points for air kills (a player's aircraft is worth more than an AI one), plus assists.
- **Ground score** = points for ground kills, one value per ground-kill category (`GROUND_CATEGORIES`), so trivial
  statics like fences (`other`) are worth very little.
- **Penalties** are subtracted from the score of the sortie's combat role: an `attack` sortie pays from its ground
  score, every other (air superiority) sortie from its air score. A penalty applies once per event: a death, a captured
  pilot, a lost aircraft, a suspected early bailout (the pilot left an aircraft nobody had touched), each friendly
  kill (up to a cap per sortie: a base bombed by mistake isn't a hundred offences).
  A sortie with a penalty and no kills has a negative score, and totals can go negative.

Everything is computed from the stored sortie columns, so a changed `[score]` section is applied by `il2ks
rebuild-aggregates` without reprocessing any mission. The values are first guesses to tune with data (product
decisions, no design doc fixes them).
"""

from dataclasses import dataclass
from typing import Final

from il2ks.core.catalog.loader import GROUND_CATEGORIES, GroundCategory


@dataclass(frozen=True, slots=True)
class ScoreRules:
    """The `[score]` config section: points per event. All values are zero or more; penalties are written positive and
    subtracted."""

    air_kill_pvp: float = 10.0  # shooting down another player's aircraft
    air_kill_ai: float = 2.0  # shooting down an AI aircraft
    air_assist: float = 3.0  # an assist on a player's or AI aircraft
    ground_tank: float = 6.0
    ground_vehicle: float = 3.0  # trucks, cars, halftracks, tractors, rocket launchers
    ground_artillery: float = 5.0
    ground_aaa: float = 5.0
    ground_ship: float = 8.0
    ground_train: float = 4.0
    ground_building: float = 2.0
    ground_parked_aircraft: float = 3.0
    ground_other: float = 0.2  # fences, crate and barrel yards, logs, small fuel tanks: trivial
    penalty_death: float = 3.0  # the pilot died
    penalty_plane_lost: float = 2.0  # the aircraft was lost (on top of a death)
    penalty_capture: float = 2.0  # the pilot was captured (on top of the lost aircraft)
    penalty_early_bailout: float = 5.0  # suspected early bailout: left an aircraft nobody had hit
    penalty_friendly_kill: float = 3.0  # per friendly kill (statics count: bombing your own base is a mistake too)
    penalty_friendly_kill_cap: float = 5.0  # at most this many friendly kills are penalised per sortie


DEFAULT_SCORE_RULES: Final = ScoreRules()


@dataclass(frozen=True, slots=True)
class SortieFacts:
    """What the score reads of one pilot sortie: the stored level-1 columns."""

    attack: bool  # combat role `attack`; anything else pays its penalties from the air score
    kills_air_pvp: int
    kills_air_ai: int
    assists: int
    kills_ground: dict[GroundCategory, int]
    is_death: bool
    is_plane_lost: bool
    is_captured: bool
    suspected_early_bailout: bool
    friendly_kills: int


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


def penalty(rules: ScoreRules, facts: SortieFacts) -> float:
    """The points taken off for what went wrong in the sortie (a positive number)."""
    return (
        rules.penalty_death * facts.is_death
        + rules.penalty_plane_lost * facts.is_plane_lost
        + rules.penalty_capture * facts.is_captured
        + rules.penalty_early_bailout * facts.suspected_early_bailout
        + rules.penalty_friendly_kill * min(facts.friendly_kills, rules.penalty_friendly_kill_cap)
    )


def score_sortie(facts: SortieFacts, rules: ScoreRules = DEFAULT_SCORE_RULES) -> SortieScore:
    """Air and ground score of one pilot sortie. Kills count for their own score whatever the combat role; penalties
    are charged to the score of the role."""
    air = rules.air_kill_pvp * facts.kills_air_pvp + rules.air_kill_ai * facts.kills_air_ai
    air += rules.air_assist * facts.assists
    ground = sum(ground_value(rules, c) * facts.kills_ground.get(c, 0) for c in GROUND_CATEGORIES)
    lost = penalty(rules, facts)
    if facts.attack:
        ground -= lost
    else:
        air -= lost
    return SortieScore(air=round(air, 4), ground=round(ground, 4))
