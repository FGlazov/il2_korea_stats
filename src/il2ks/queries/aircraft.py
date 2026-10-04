"""Reads behind the aircraft pages (FR-WEB-8). Simple SELECTs only: the numbers were aggregated at ingest (TD-22).

    stats_list(sort)         -> list[AircraftStats]      # one row per flown type; one query
    stats_for(aircraft_id)   -> AircraftStats | None     # one query
    matchups(aircraft, tour, intercept, sort) -> MatchupTable  # kills and losses against each enemy type; one query
    top_elo(aircraft, rules) -> list[PlayerAircraft]     # best pilots by per-type Elo (visible only); one query
    top_ground(aircraft, rules) -> list[BoardRow]        # ... by ground score per hour on target; one query
    payloads(aircraft)       -> list[AircraftPayload]    # one query

The totals include hidden players; only the named top pilots leave them out. Top pilots are ranked by skill, not by
volume (maintainer, OQ-49/50): the per-type Elo of fighter-vs-fighter combat (`PlayerAircraft.elo`, computed at ingest
by `ingest.ratings`) and, for attack work, the ground score per hour on target.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from django.db.models import Q

from il2ks.config import LeaderboardConfig
from il2ks.db.models import AircraftMatchup, AircraftPayload, AircraftStats, GameObject, PlayerAircraft, Tour
from il2ks.queries.leaderboards import BOARDS, BoardRow, top_rows
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
}
DEFAULT_AIRCRAFT_SORT = "-sorties"

TOP_PILOTS = 10


def stats_list(sort: str) -> list[AircraftStats]:
    """Every flown type, ordered by a resolved `sort` ('kills_air' or '-kills_air', see `players.resolve_sort`)."""
    order = order_by(AIRCRAFT_SORTS[sort.removeprefix("-")], sort)
    return list(AircraftStats.objects.select_related("aircraft").order_by(order, "aircraft__display_name", "pk"))


def stats_for(aircraft_id: int) -> AircraftStats | None:
    return AircraftStats.objects.select_related("aircraft").filter(aircraft_id=aircraft_id).first()


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


def payloads(aircraft: GameObject) -> list[AircraftPayload]:
    """Loadouts flown in the type, most used first."""
    return list(AircraftPayload.objects.filter(aircraft=aircraft).order_by("-sorties", "payload_name"))
