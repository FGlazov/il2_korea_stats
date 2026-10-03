"""Reads behind the aircraft pages (FR-WEB-8). Simple SELECTs only: the numbers were aggregated at ingest (TD-22).

    stats_list(sort)         -> list[AircraftStats]      # one row per flown type; one query
    stats_for(aircraft_id)   -> AircraftStats | None     # one query
    matchups(aircraft)       -> list[Matchup]            # kills and losses against each enemy type; two queries
    top_pilots(aircraft)     -> list[PlayerAircraft]     # visible players only (FR-ADM-3); one query
    payloads(aircraft)       -> list[AircraftPayload]    # one query

The totals include hidden players; only the named top pilots leave them out.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from il2ks.db.models import AircraftMatchup, AircraftPayload, AircraftStats, GameObject, PlayerAircraft

# Public `?sort=` key -> the AircraftStats column it orders by (a whitelist; anything else falls back to the default).
AIRCRAFT_SORTS: Mapping[str, str] = {
    "aircraft": "aircraft__display_name",
    "sorties": "sorties",
    "pilots": "pilots",
    "flight_time_s": "flight_time_s",
    "kills_air": "kills_air",
    "kills_ground": "kills_ground",
    "deaths": "deaths",
    "planes_lost": "planes_lost",
    "kd": "kd",
    "kl": "kl",
    "survival": "survival",
    "attack_share": "attack_share",
}
DEFAULT_AIRCRAFT_SORT = "-sorties"

TOP_PILOTS = 10
MIN_PILOT_SORTIES = 5
"""A pilot needs this many sorties in the type to be named among its top pilots (one lucky sortie is no ranking)."""


def stats_list(sort: str) -> list[AircraftStats]:
    """Every flown type, ordered by a resolved `sort` ('kills_air' or '-kills_air', see `players.resolve_sort`)."""
    column = AIRCRAFT_SORTS[sort.removeprefix("-")]
    order = f"{'-' if sort.startswith('-') else ''}{column}"
    return list(AircraftStats.objects.select_related("aircraft").order_by(order, "aircraft__display_name", "pk"))


def stats_for(aircraft_id: int) -> AircraftStats | None:
    return AircraftStats.objects.select_related("aircraft").filter(aircraft_id=aircraft_id).first()


@dataclass(frozen=True, slots=True)
class Matchup:
    """Player-versus-player air kills between the page's type and one enemy type."""

    enemy: GameObject
    kills: int  # the page's type shot down this enemy
    losses: int  # this enemy shot down the page's type

    @property
    def encounters(self) -> int:
        return self.kills + self.losses


def matchups(aircraft: GameObject) -> list[Matchup]:
    """Kills and losses against every type met, the most contested first (ties by name)."""
    kills = {
        row.victim_aircraft_id: row
        for row in AircraftMatchup.objects.filter(killer_aircraft=aircraft).select_related("victim_aircraft")
    }
    losses = {
        row.killer_aircraft_id: row
        for row in AircraftMatchup.objects.filter(victim_aircraft=aircraft).select_related("killer_aircraft")
    }
    out: list[Matchup] = []
    for enemy_id in kills.keys() | losses.keys():
        won, lost = kills.get(enemy_id), losses.get(enemy_id)
        enemy = won.victim_aircraft if won is not None else lost.killer_aircraft if lost is not None else None
        if enemy is not None:
            out.append(Matchup(enemy, won.kills if won else 0, lost.kills if lost else 0))
    return sorted(out, key=lambda m: (-m.encounters, m.enemy.display_name))


def top_pilots(aircraft: GameObject) -> list[PlayerAircraft]:
    """The best pilots in the type by air kills (then fewer deaths), visible players with enough sorties only."""
    rows = PlayerAircraft.objects.filter(
        aircraft=aircraft, player__is_hidden=False, sorties__gte=MIN_PILOT_SORTIES
    ).select_related("player")
    return list(rows.order_by("-kills_air", "deaths", "-sorties", "pk")[:TOP_PILOTS])


def payloads(aircraft: GameObject) -> list[AircraftPayload]:
    """Loadouts flown in the type, most used first."""
    return list(AircraftPayload.objects.filter(aircraft=aircraft).order_by("-sorties", "payload_name"))
