"""Reads behind the aircraft pages (FR-WEB-8). Simple SELECTs only: the numbers were aggregated at ingest (TD-22).

    stats_list(sort)         -> list[AircraftStats]      # one row per flown type; one query
    stats_for(aircraft_id)   -> AircraftStats | None     # one query
    matchups(aircraft, tour, intercept, sort, role, mod_pattern) -> MatchupTable  # kills and losses per enemy type
    top_elo(aircraft, rules, tour, role, mod_pattern) -> list[EloRow]  # best pilots by per-type Elo (visible only)
    top_ground(aircraft, rules, tour, role, mod_pattern) -> list[BoardRow]  # ... by ground score per hour on target
    scoped_stats(aircraft, tour, role, mod_pattern) -> TourAircraftStats  # a tour / role / mod filter scope; one query
    payloads(aircraft, role, rules, sort, mod_pattern, tour) -> list[Loadout]  # loadouts with effectiveness; one query
    mod_sets(aircraft, role, rules, sort, mod_pattern, tour) -> list[ModSet]   # weapon-mod sets, same measures
    significant_mods(aircraft) -> tuple[SignificantMod, ...]  # the mods the page can filter by (catalog, no query)

The totals include hidden players; only the named top pilots leave them out. Top pilots are ranked by skill, not by
volume (maintainer, OQ-49/50): the per-type Elo of fighter-vs-fighter combat (`PlayerAircraft.elo`, computed at ingest
by `ingest.ratings`) and, for attack work, the ground score per hour on target. Every section follows the page's scope
(tour, role, modification filter; maintainer 2026-10-04), each from stored rows of that scope. The per-type Elo is all
time by nature: in a narrower scope pilots are still ranked by it, but only those with enough air superiority sorties
within the scope (the sorties column is the scope's); the ground score per hour is computed from the scope's own rows.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from typing import Final

from django.db.models import Q

from il2ks.config import LeaderboardConfig
from il2ks.core.catalog.loader import (
    MOD_ANY,
    MOD_WITH,
    MOD_WITHOUT,
    Catalog,
    load_default_catalog,
    mod_filter_pattern,
)
from il2ks.db.models import (
    AircraftMatchup,
    AircraftMods,
    AircraftPayload,
    AircraftRole,
    AircraftStats,
    GameObject,
    Player,
    PlayerAircraft,
    PlayerAircraftScope,
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
        # tour None: the all-time role rows (null tour); never a modification-filter row (those are `scoped_stats`)
        rows = TourAircraftStats.objects.filter(tour=tour, role=role, mod_pattern="")
    return list(rows.select_related("aircraft").order_by(order, "aircraft__display_name", "pk"))


def stats_for(aircraft_id: int) -> AircraftStats | None:
    return AircraftStats.objects.select_related("aircraft").filter(aircraft_id=aircraft_id).first()


def scoped_stats(
    aircraft: GameObject, tour: Tour | None, role: AircraftRole, mod_pattern: str = ""
) -> TourAircraftStats:
    """The type's counters in `tour` (None = all time), `role` and modification filter `mod_pattern` ('' = none); all
    zero (an unsaved row) when nobody flew it there. Not for all time, every role and no filter: that is
    `AircraftStats`. One query."""
    found = TourAircraftStats.objects.filter(aircraft=aircraft, tour=tour, role=role, mod_pattern=mod_pattern).first()
    return found or TourAircraftStats(aircraft=aircraft, tour=tour, role=role, mod_pattern=mod_pattern)


@cache
def _catalog() -> Catalog:
    return load_default_catalog()


@dataclass(frozen=True, slots=True)
class SignificantMod:
    """A modification the aircraft page can filter by (`weapon_mods.csv`, `significant`)."""

    mod_id: int
    name: str


MOD_PARAM_PREFIX = "mod"
"""`?mod5=with|without` filters the page by one significant modification (absent = any)."""
MOD_STATES: Final[Mapping[str, str]] = {"any": MOD_ANY, "with": MOD_WITH, "without": MOD_WITHOUT}


def significant_mods(aircraft: GameObject) -> tuple[SignificantMod, ...]:
    """The type's significant modifications, by id; empty for most types (no filter is offered then)."""
    return tuple(SignificantMod(m.mod_id, m.name) for m in _catalog().significant_mods(aircraft.log_name))


def mod_names(log_name: str, weapon_mods: int) -> tuple[str, ...]:
    """The names of the modifications a `WM` bitmask selects for the aircraft type with this log name, by id (the
    catalog's name; an id it doesn't know shows as `#id`); empty = no modification."""
    return tuple(
        name if name is not None else f"#{mod_id}" for mod_id, name in _catalog().weapon_mods(log_name, weapon_mods)
    )


def mod_param(mod_id: int) -> str:
    return f"{MOD_PARAM_PREFIX}{mod_id}"


def mod_states(params: Mapping[str, str], significant: tuple[SignificantMod, ...]) -> tuple[str, ...]:
    """The state of each significant mod from the query (`any` / `with` / `without`); anything else means any."""
    return tuple(raw if (raw := params.get(mod_param(m.mod_id), "any")) in MOD_STATES else "any" for m in significant)


def mod_pattern_of(states: tuple[str, ...]) -> str:
    """The stored pattern of these states ('' = unfiltered), see `TourAircraftStats.mod_pattern`."""
    return mod_filter_pattern([MOD_STATES[s] for s in states])


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
    aircraft: GameObject,
    tour: Tour | None = None,
    intercept: bool = False,
    sort: str = DEFAULT_MATCHUP_SORT,
    role: AircraftRole = AircraftRole.ALL,
    mod_pattern: str = "",
) -> MatchupTable:
    """Kills and losses against every enemy type met, in `tour` (None = all time), for all fights or only intercept
    fights (both sorties air superiority, `AircraftMatchup.intercept`). `role` and `mod_pattern` are those of THIS
    type's sortie: the killer's for its kills, the victim's for its losses (`AircraftMatchup.scoped_side`). `sort`: a
    `MATCHUP_SORTS` name, `-` for descending; ties by name. One query."""
    if role == AircraftRole.ALL and not mod_pattern:
        sides = Q(killer_aircraft=aircraft, scoped_side="") | Q(victim_aircraft=aircraft, scoped_side="")
    else:
        sides = Q(killer_aircraft=aircraft, scoped_side="killer") | Q(victim_aircraft=aircraft, scoped_side="victim")
    rows = AircraftMatchup.objects.filter(
        sides, tour=tour, intercept=intercept, combat_role=role, mod_pattern=mod_pattern
    ).select_related("killer_aircraft", "victim_aircraft")
    kills: dict[int, tuple[GameObject, int]] = {}
    losses: dict[int, tuple[GameObject, int]] = {}
    scoped = role != AircraftRole.ALL or bool(mod_pattern)
    for row in rows:
        # Scoped: a pair of one type against itself has both a killer and a victim row, each feeds only its column.
        if row.killer_aircraft_id == aircraft.pk and (not scoped or row.scoped_side == "killer"):
            kills[row.victim_aircraft_id] = (row.victim_aircraft, row.kills)
        if row.victim_aircraft_id == aircraft.pk and (not scoped or row.scoped_side == "victim"):
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


@dataclass(frozen=True, slots=True)
class EloRow:
    """A row of the top-Elo table: the pilot, their Elo and its encounters (all time), and the sorties in the scope."""

    player: Player
    elo: float
    elo_games: int
    sorties: int

    @property
    def player_id(self) -> int:
        return self.player.pk


def is_alltime_scope(tour: Tour | None, role: AircraftRole, mod_pattern: str) -> bool:
    """Whether the scope is all time, every role, no filter: the one `PlayerAircraft` itself covers."""
    return tour is None and role == AircraftRole.ALL and not mod_pattern


def top_elo(
    aircraft: GameObject,
    rules: LeaderboardConfig,
    tour: Tour | None = None,
    role: AircraftRole = AircraftRole.ALL,
    mod_pattern: str = "",
) -> list[EloRow]:
    """The type's best pilots by their Elo in it (OQ-49): visible players with enough rated games, the best first. The
    Elo is all time by nature (`PlayerAircraft.elo`); in a narrower scope (tour, role, filter) a pilot must also have
    flown at least the leaderboard minimum of air superiority sorties within it, and `sorties` are the scope's. One
    query for the all-time scope, two for a narrower one (the scope's pilots, then their Elo)."""
    minimum = max(rules.min_elo_games, 1)
    order = ("-elo", "-elo_games", "player__name_lower", "pk")
    rated = PlayerAircraft.objects.filter(aircraft=aircraft, player__is_hidden=False, elo_games__gte=minimum)
    if is_alltime_scope(tour, role, mod_pattern):
        return [
            EloRow(r.player, r.elo, r.elo_games, r.sorties)
            for r in rated.select_related("player").order_by(*order)[:TOP_PILOTS]
        ]
    in_scope = dict(
        PlayerAircraftScope.objects.filter(
            aircraft=aircraft,
            tour=tour,
            role=role,
            mod_pattern=mod_pattern,
            air_superiority_sorties__gte=max(rules.min_air_superiority_sorties, 1),
        ).values_list("player_id", "sorties")
    )
    best = rated.filter(player_id__in=list(in_scope)).select_related("player").order_by(*order)[:TOP_PILOTS]
    return [EloRow(r.player, r.elo, r.elo_games, in_scope[r.player_id]) for r in best]


def top_ground(
    aircraft: GameObject,
    rules: LeaderboardConfig,
    tour: Tour | None = None,
    role: AircraftRole = AircraftRole.ALL,
    mod_pattern: str = "",
) -> list[BoardRow]:
    """The type's best attack pilots by ground score per hour on target (FR-WEB-20) within the scope, under the same
    minimums as the ground-per-hour board (counted from the scope's own sorties)."""
    return top_rows(BOARDS["ground-hour"], rules, TOP_PILOTS, aircraft, tour, role, mod_pattern)


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


type EffectivenessRow = AircraftPayload | AircraftMods
"""A stored "effectiveness by X" row: loadouts or weapon-mod sets (the shared columns of `AircraftEffectiveness`);
`measures_of` reads nothing else. A new grouping adds its model to this union."""


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
    mod_pattern: str = "",
    tour: Tour | None = None,
) -> list[Loadout]:
    """Loadouts flown in the type (in `tour`, None = all time; only those of `role` unless `all`; within the
    modification filter `mod_pattern`, '' = none) with their effectiveness measures, ordered by a resolved `sort` (a
    `LOADOUT_SORTS` name, `-` for descending; a dash measure sorts last either way, ties by sorties, then name). One
    query."""
    rows = AircraftPayload.objects.filter(aircraft=aircraft, tour=tour, mod_pattern=mod_pattern)
    if role != AircraftRole.ALL:
        rows = rows.filter(combat_role=role)
    used = rules or LeaderboardConfig()
    return _ordered(
        [_loadout(row, used) for row in rows],
        sort,
        lambda m: m.payload,
        lambda m: Measures(m.elo, m.kills_per_sortie, m.kd, m.ground_hour),
        lambda m: m.payload.payload_name.casefold(),
        "loadout",
    )


MOD_SORTS: tuple[str, ...] = ("mods", *LOADOUT_SORTS[1:])
DEFAULT_MOD_SORT = "-sorties"


@dataclass(frozen=True, slots=True)
class ModSet:
    """One weapon-mod set row of the aircraft page with its effectiveness measures (see `Loadout`). `names` are the
    modifications of the set by id (`weapon_mods.csv`; an id the catalog doesn't know shows as `#id`), empty = none
    chosen."""

    stats: AircraftMods
    names: tuple[str, ...]
    elo: float | None
    kills_per_sortie: float | None
    kd: float | None
    ground_hour: float | None

    @property
    def label(self) -> str:
        return " + ".join(self.names)


def mod_sets(
    aircraft: GameObject,
    role: AircraftRole = AircraftRole.ALL,
    rules: LeaderboardConfig | None = None,
    sort: str = DEFAULT_MOD_SORT,
    mod_pattern: str = "",
    tour: Tour | None = None,
) -> list[ModSet]:
    """The weapon-mod sets flown in the type, as `payloads` does for loadouts (same role, filter, measures and sort
    rules; `sort` a `MOD_SORTS` name). One query; the names come from the shipped catalog."""
    rows = AircraftMods.objects.filter(aircraft=aircraft, tour=tour, mod_pattern=mod_pattern)
    if role != AircraftRole.ALL:
        rows = rows.filter(combat_role=role)
    used = rules or LeaderboardConfig()
    found: list[ModSet] = []
    for row in rows:
        m = measures_of(row, used)
        found.append(
            ModSet(row, mod_names(aircraft.log_name, row.weapon_mods), m.elo, m.kills_per_sortie, m.kd, m.ground_hour)
        )
    return _ordered(
        found,
        sort,
        lambda m: m.stats,
        lambda m: Measures(m.elo, m.kills_per_sortie, m.kd, m.ground_hour),
        lambda m: m.label.casefold(),
        "mods",
    )


def _ordered[R](
    found: list[R],
    sort: str,
    stats: Callable[[R], EffectivenessRow],
    measures: Callable[[R], Measures],
    label: Callable[[R], str],
    label_sort: str,
) -> list[R]:
    """The effectiveness rows in the order of a resolved `sort`: by the row's label (`label_sort`) or a counter or
    measure; a dash measure sorts last either way, the tie order is sorties (most first), then the label."""
    found.sort(key=label)
    found.sort(key=lambda r: stats(r).sorties, reverse=True)  # the tie order of every sort
    key = sort.removeprefix("-")
    descending = sort.startswith("-")
    if key == label_sort:
        found.sort(key=label, reverse=descending)
        return found
    values: dict[str, Callable[[R], float | None]] = {
        "sorties": lambda r: stats(r).sorties,
        "kills_air": lambda r: stats(r).kills_air,
        "kills_ground": lambda r: stats(r).kills_ground,
        "deaths": lambda r: stats(r).deaths,
        "elo": lambda r: measures(r).elo,
        "kills_per_sortie": lambda r: measures(r).kills_per_sortie,
        "kd": lambda r: measures(r).kd,
        "ground_hour": lambda r: measures(r).ground_hour,
    }
    value = values[key]
    defined = sorted((r for r in found if value(r) is not None), key=lambda r: value(r) or 0.0, reverse=descending)
    return defined + [r for r in found if value(r) is None]
