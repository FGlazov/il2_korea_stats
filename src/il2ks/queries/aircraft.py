"""Reads behind the aircraft pages (FR-WEB-8). Simple SELECTs only: the numbers were aggregated at ingest (TD-22).

    stats_list(sort)         -> list[AircraftStats]      # one row per flown type; one query
    stats_for(aircraft_id)   -> AircraftStats | None     # one query
    matchups(aircraft, tour, intercept, sort) -> MatchupTable  # kills and losses against each enemy type; one query
    top_elo(aircraft, rules) -> list[PlayerAircraft]     # best pilots by per-type Elo (visible only); one query
    top_ground(aircraft, rules) -> list[BoardRow]        # ... by ground score per hour on target; one query
    scoped_stats(aircraft, tour, role) -> TourAircraftStats  # counters in a tour and/or role; one query
    payloads(aircraft, role, rules, sort) -> list[Loadout]   # loadouts with their effectiveness measures; one query

The totals include hidden players; only the named top pilots leave them out. Top pilots are ranked by skill, not by
volume (maintainer, OQ-49/50): the per-type Elo of fighter-vs-fighter combat (`PlayerAircraft.elo`, computed at ingest
by `ingest.ratings`) and, for attack work, the ground score per hour on target.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from django.db.models import Q

from il2ks.config import LeaderboardConfig
from il2ks.db.models import (
    AircraftMatchup,
    AircraftPayload,
    AircraftRole,
    AircraftStats,
    GameObject,
    PlayerAircraft,
    Tour,
    TourAircraftStats,
)
from il2ks.queries.leaderboards import BOARDS, SECONDS_PER_HOUR, BoardRow, top_rows
from il2ks.queries.sorting import Ratio, SortSpec, order_by

# Public `?sort=` key -> what it orders by: an AircraftStats column or a NULL-safe ratio of two columns (no ratio is
# stored, OQ-98; a whitelist, anything else falls back to the default). The first block is the default columns, the
# second the optional ones a visitor can add with `?cols=` (`web.columns.AIRCRAFT_COLUMNS`; a test keeps the two in
# step).
AIRCRAFT_SORTS: Mapping[str, SortSpec] = {
    "aircraft": "aircraft__display_name",
    "sorties": "sorties",
    "pilots": "pilots",
    "flight_time_s": "flight_time_s",
    "kills_air": "kills_air",
    "kills_ground": "kills_ground",
    "deaths": "deaths",
    "planes_lost": "planes_lost",
    "kd": Ratio("kills_air", "deaths"),
    "kl": Ratio("kills_air", "planes_lost"),
    "survival": Ratio("sorties", "sorties", minus="deaths"),
    "attack_share": Ratio("attack_sorties", "sorties"),
    "kills_air_pvp": "kills_air_pvp",
    "assists": "assists",
    "bailouts": "bailouts",
    "friendly_kills": "friendly_kills",
    "score_air": "score_air",
    "score_ground": "score_ground",
    "ground_hour": Ratio("score_ground_attack", "time_on_target_s", scale=3600.0),
    "sortie_length": Ratio("flight_time_s", "sorties"),
    "kills_per_hour": Ratio("kills_air", "flight_time_s", scale=3600.0),
    "sorties_per_pilot": Ratio("sorties", "pilots"),
    "accuracy": Ratio("accuracy_hits", "accuracy_rounds"),
    "accuracy_air": Ratio("accuracy_air_hits", "accuracy_air_rounds"),
    "accuracy_ground": Ratio("accuracy_ground_hits", "accuracy_ground_rounds"),
}
DEFAULT_AIRCRAFT_SORT = "-sorties"

type StatsRow = AircraftStats | TourAircraftStats
"""A row of the aircraft list: the all-time counters or the selected tour's (same counters, `AircraftCounters`)."""

TOP_PILOTS = 10


ROLE_PARAM = "role"
ROLES: Final[tuple[AircraftRole, ...]] = (AircraftRole.ALL, AircraftRole.AIR_SUPERIORITY, AircraftRole.ATTACK)


def parse_role(raw: str | None) -> AircraftRole:
    """`?role=air_superiority|attack`; absent or anything else means every role."""
    return next((role for role in ROLES if role.value == raw), AircraftRole.ALL)


def stats_list(sort: str, tour: Tour | None = None, role: AircraftRole = AircraftRole.ALL) -> list[StatsRow]:
    """Every type flown in `tour` (None = all time) in `role` (`all` = every sortie), ordered by a resolved `sort`
    ('kills_air' or '-kills_air', see `players.resolve_sort`). One query."""
    order = order_by(AIRCRAFT_SORTS[sort.removeprefix("-")], sort)
    if role == AircraftRole.ALL and tour is None:
        rows = AircraftStats.objects.all()
    else:
        rows = TourAircraftStats.objects.filter(tour=tour, role=role)  # tour None: the all-time role rows (null tour)
    return list(rows.select_related("aircraft").order_by(order, "aircraft__display_name", "pk"))


def stats_for(aircraft_id: int) -> AircraftStats | None:
    return AircraftStats.objects.select_related("aircraft").filter(aircraft_id=aircraft_id).first()


def scoped_stats(aircraft: GameObject, tour: Tour | None, role: AircraftRole) -> TourAircraftStats:
    """The type's counters in `tour` (None = all time) and `role`; all zero (an unsaved row) when nobody flew it
    there. Not for all time and every role: that is `AircraftStats`. One query."""
    found = TourAircraftStats.objects.filter(aircraft=aircraft, tour=tour, role=role).first()
    return found or TourAircraftStats(aircraft=aircraft, tour=tour, role=role)


MIN_ENCOUNTERS = 10
"""A matchup shows its exchange ratio (and can be named best or worst) only with at least this many kills plus
losses in the selected scope: with fewer, one lucky mission decides the number (PRODUCT decision)."""

MATCHUP_SORTS: tuple[str, ...] = ("enemy", "kills", "losses", "encounters", "ratio")
DEFAULT_MATCHUP_SORT = "-encounters"


@dataclass(frozen=True, slots=True)
class Matchup:
    """Player-versus-player air kills between the page's type and one enemy type."""

    enemy: GameObject
    kills: int  # the page's type shot down this enemy
    losses: int  # this enemy shot down the page's type

    @property
    def encounters(self) -> int:
        return self.kills + self.losses

    @property
    def rated(self) -> bool:
        """Enough encounters for the ratio to mean something (`MIN_ENCOUNTERS`)."""
        return self.encounters >= MIN_ENCOUNTERS

    @property
    def share(self) -> float:
        """Kills as a share of all kills and losses in the matchup (0.0 without any): finite where kills per loss is
        not, so it ranks the matchups."""
        return self.kills / self.encounters if self.encounters else 0.0


@dataclass(frozen=True, slots=True)
class MatchupTable:
    """The matchup rows of one scope in display order, and the best and worst rated matchups (None unless at least two
    matchups are rated and they differ)."""

    rows: list[Matchup]
    best: Matchup | None
    worst: Matchup | None


def matchups(
    aircraft: GameObject, tour: Tour | None = None, intercept: bool = False, sort: str = DEFAULT_MATCHUP_SORT
) -> MatchupTable:
    """Kills and losses against every enemy type met, in `tour` (None = all time), for all fights or only intercept
    fights (both sorties air superiority, `AircraftMatchup.intercept`). `sort`: a `MATCHUP_SORTS` name, `-` for
    descending; ties by name. One query."""
    rows = AircraftMatchup.objects.filter(
        Q(killer_aircraft=aircraft) | Q(victim_aircraft=aircraft), tour=tour, intercept=intercept
    ).select_related("killer_aircraft", "victim_aircraft")
    kills: dict[int, tuple[GameObject, int]] = {}
    losses: dict[int, tuple[GameObject, int]] = {}
    for row in rows:
        if row.killer_aircraft_id == aircraft.pk:
            kills[row.victim_aircraft_id] = (row.victim_aircraft, row.kills)
        if row.victim_aircraft_id == aircraft.pk:
            losses[row.killer_aircraft_id] = (row.killer_aircraft, row.kills)
    found = [
        Matchup(
            (kills.get(enemy_id) or losses[enemy_id])[0],
            kills.get(enemy_id, (aircraft, 0))[1],
            losses.get(enemy_id, (aircraft, 0))[1],
        )
        for enemy_id in kills.keys() | losses.keys()
    ]
    found.sort(key=lambda m: m.enemy.display_name)  # the tie order of every sort
    key = sort.removeprefix("-")
    descending = sort.startswith("-")
    values: dict[str, Callable[[Matchup], float | str]] = {
        "enemy": lambda m: m.enemy.display_name.casefold(),
        "kills": lambda m: m.kills,
        "losses": lambda m: m.losses,
        "encounters": lambda m: m.encounters,
        "ratio": lambda m: m.share if m.rated else -1.0,  # unrated rows last when descending, first when ascending
    }
    found.sort(key=values[key], reverse=descending)
    rated = sorted((m for m in found if m.rated), key=lambda m: (-m.share, m.enemy.display_name))
    best, worst = (rated[0], rated[-1]) if len(rated) >= 2 and rated[0].share != rated[-1].share else (None, None)
    return MatchupTable(found, best, worst)


def top_elo(aircraft: GameObject, rules: LeaderboardConfig) -> list[PlayerAircraft]:
    """The type's best pilots by their Elo in it (OQ-49): visible players with enough rated games, the best first."""
    rows = PlayerAircraft.objects.filter(
        aircraft=aircraft, player__is_hidden=False, elo_games__gte=max(rules.min_elo_games, 1)
    ).select_related("player")
    return list(rows.order_by("-elo", "-elo_games", "player__name_lower", "pk")[:TOP_PILOTS])


def top_ground(aircraft: GameObject, rules: LeaderboardConfig) -> list[BoardRow]:
    """The type's best attack pilots by ground score per hour on target (FR-WEB-20), under the same minimums as the
    ground-per-hour board."""
    return top_rows(BOARDS["ground-hour"], rules, TOP_PILOTS, aircraft)


LOADOUT_SORTS: tuple[str, ...] = (
    "loadout",
    "sorties",
    "kills_air",
    "kills_ground",
    "deaths",
    "elo",
    "kills_per_sortie",
    "kd",
    "ground_hour",
)
DEFAULT_LOADOUT_SORT = "-sorties"


@dataclass(frozen=True, slots=True)
class Loadout:
    """One loadout row of the aircraft page with its effectiveness measures (None = does not apply or too few sorties:
    shown as a dash). Air superiority loadouts carry the average pilot Elo, air kills per sortie and K/D (PvP air
    kills per death, a loadout without a death counting its kills); attack loadouts the ground score per hour on
    target. Every measure is a division of stored columns (TD-22); the Elo average is stored
    (`AircraftPayload.elo_avg`)."""

    payload: AircraftPayload
    elo: float | None
    kills_per_sortie: float | None
    kd: float | None
    ground_hour: float | None


type EffectivenessRow = AircraftPayload
"""A stored "effectiveness by X" row: `AircraftPayload` today. A new grouping (weapon mods) with the same columns
(combat_role, sorties, kills_air, deaths, kills_air_pvp, score_ground_attack, time_on_target_s, elo_avg) is added to
this union; `measures_of` reads nothing else. (A structural Protocol does not type-check against Django fields.)"""


@dataclass(frozen=True, slots=True)
class Measures:
    """The effectiveness measures of one row (None = a dash): average pilot Elo, air kills per sortie, K/D (PvP air
    kills per death, a row without a death counting its kills) for air superiority; ground score per hour on target
    for attack."""

    elo: float | None = None
    kills_per_sortie: float | None = None
    kd: float | None = None
    ground_hour: float | None = None


def measures_of(row: EffectivenessRow, rules: LeaderboardConfig) -> Measures:
    """The one definition of the effectiveness measures, shared by every table (loadouts, later weapon mods): each
    only above the leaderboard minimums of the row's role (one lucky sortie must not top the table): air superiority
    sorties as the interception board asks, attack sorties and time on target as the ground-per-hour board does."""
    if row.combat_role == AircraftRole.AIR_SUPERIORITY:
        if row.sorties < max(rules.min_air_superiority_sorties, 1):
            return Measures()
        return Measures(row.elo_avg, row.kills_air / row.sorties, row.kills_air_pvp / max(row.deaths, 1))
    if row.combat_role == AircraftRole.ATTACK:
        enough = row.sorties >= max(rules.min_attack_sorties, 1) and row.time_on_target_s >= max(
            rules.min_time_on_target_minutes * 60.0, 1.0
        )
        hour = row.score_ground_attack * SECONDS_PER_HOUR / row.time_on_target_s if enough else None
        return Measures(ground_hour=hour)
    return Measures()


def _loadout(row: AircraftPayload, rules: LeaderboardConfig) -> Loadout:
    m = measures_of(row, rules)
    return Loadout(row, m.elo, m.kills_per_sortie, m.kd, m.ground_hour)


def payloads(
    aircraft: GameObject,
    role: AircraftRole = AircraftRole.ALL,
    rules: LeaderboardConfig | None = None,
    sort: str = DEFAULT_LOADOUT_SORT,
) -> list[Loadout]:
    """Loadouts flown in the type (all time; only those of `role` unless `all`) with their effectiveness measures,
    ordered by a resolved `sort` (a `LOADOUT_SORTS` name, `-` for descending; a dash measure sorts last either way,
    ties by sorties, then name). One query."""
    rows = AircraftPayload.objects.filter(aircraft=aircraft)
    if role != AircraftRole.ALL:
        rows = rows.filter(combat_role=role)
    used = rules or LeaderboardConfig()
    found = [_loadout(row, used) for row in rows]
    found.sort(key=lambda m: m.payload.payload_name.casefold())
    found.sort(key=lambda m: m.payload.sorties, reverse=True)  # the tie order of every sort
    key = sort.removeprefix("-")
    descending = sort.startswith("-")
    if key == "loadout":
        found.sort(key=lambda m: m.payload.payload_name.casefold(), reverse=descending)
        return found
    measures: dict[str, Callable[[Loadout], float | None]] = {
        "sorties": lambda m: m.payload.sorties,
        "kills_air": lambda m: m.payload.kills_air,
        "kills_ground": lambda m: m.payload.kills_ground,
        "deaths": lambda m: m.payload.deaths,
        "elo": lambda m: m.elo,
        "kills_per_sortie": lambda m: m.kills_per_sortie,
        "kd": lambda m: m.kd,
        "ground_hour": lambda m: m.ground_hour,
    }
    measure = measures[key]
    defined = sorted((m for m in found if measure(m) is not None), key=lambda m: measure(m) or 0.0, reverse=descending)
    return defined + [m for m in found if measure(m) is None]
