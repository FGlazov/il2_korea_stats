"""Reads for the sortie pages (FR-WEB-5, FR-WEB-6): simple SELECTs with simple joins only (TD-22).

Hiding (FR-ADM-3): a sortie of a hidden player or of a hidden mission does not exist for the public pages
(`visible_sortie` answers None, the sortie list leaves hidden missions out).
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from django.core.paginator import Page, Paginator

from il2ks.db.models import (
    CombatRole,
    GameObject,
    Kill,
    Outcome,
    Player,
    PlayerAircraft,
    PlayerSortie,
    Role,
    Tour,
)

PAGE_SIZE = 25

# `?sort=` whitelist: public field name -> ORM ordering. Ties are broken by the primary key, so paging is stable.
SORT_FIELDS: dict[str, str] = {
    "date": "spawned_at",
    "mission": "mission__started_at",
    "aircraft": "aircraft__display_name",
    "outcome": "outcome",
    "kills_air": "kills_air",
    "kills_ground": "kills_ground",
    "assists": "assists",
    "flight_time": "flight_time_s",
}
DEFAULT_SORT = "-date"


def resolve_sort(raw: str) -> str:
    """The whitelisted `?sort=` value ('date', '-date', ...); anything else falls back to newest first."""
    field = raw.removeprefix("-")
    return raw if field in SORT_FIELDS else DEFAULT_SORT


@dataclass(frozen=True, slots=True)
class SortieFilters:
    """The validated `?aircraft=`, `?outcome=`, `?role=`, `?combat_role=` of the list ('' = no filter) and the chosen
    tour (`?tour=`, resolved by `queries.tours.tour_choice_from`; None = all time)."""

    aircraft: int | None = None
    outcome: str = ""
    role: str = ""
    combat_role: str = ""
    tour: Tour | None = None


def parse_filters(raw: dict[str, str], aircraft_ids: Collection[int]) -> SortieFilters:
    """Filters from raw query values; a value that is not one of the offered choices is ignored, never an error."""
    aircraft = int(raw["aircraft"]) if raw.get("aircraft", "").isdecimal() else None
    return SortieFilters(
        aircraft=aircraft if aircraft in aircraft_ids else None,
        outcome=raw.get("outcome", "") if raw.get("outcome", "") in Outcome.values else "",
        role=raw.get("role", "") if raw.get("role", "") in Role.values else "",
        combat_role=raw.get("combat_role", "") if raw.get("combat_role", "") in CombatRole.values else "",
    )


def player_aircraft(player: Player) -> list[PlayerAircraft]:
    """The aircraft types a player has flown (the filter's choices), by name."""
    rows = PlayerAircraft.objects.filter(player=player).select_related("aircraft")
    return list(rows.order_by("aircraft__display_name", "aircraft_id"))


def sortie_page(player: Player, filters: SortieFilters, sort: str, number: str) -> Page:
    """One page of a player's sorties in visible missions: filtered, sorted (`sort` already resolved) and paginated."""
    rows = PlayerSortie.objects.filter(player=player, mission__is_hidden=False).select_related("mission", "aircraft")
    if filters.tour is not None:
        rows = rows.filter(mission__tour=filters.tour)
    if filters.aircraft is not None:
        rows = rows.filter(aircraft_id=filters.aircraft)
    if filters.outcome:
        rows = rows.filter(outcome=filters.outcome)
    if filters.role:
        rows = rows.filter(role=filters.role)
    if filters.combat_role:
        rows = rows.filter(combat_role=filters.combat_role)
    field = SORT_FIELDS[sort.removeprefix("-")]
    rows = rows.order_by(f"-{field}" if sort.startswith("-") else field, "-pk" if sort.startswith("-") else "pk")
    return Paginator(rows, PAGE_SIZE).get_page(number)


def visible_sortie(pk: int) -> PlayerSortie | None:
    """The sortie with its player, mission and aircraft, or None when it does not exist or is hidden (404)."""
    return (
        PlayerSortie.objects.filter(pk=pk, player__is_hidden=False, mission__is_hidden=False)
        .select_related("player", "mission", "aircraft")
        .first()
    )


def kills_made(sortie: PlayerSortie) -> list[Kill]:
    """PvP kill rows where this sortie is the killer (kills, assists and friendly fire), in time order."""
    return list(Kill.objects.filter(killer_sortie=sortie).order_by("tick", "pk"))


def kills_suffered(sortie: PlayerSortie) -> list[Kill]:
    """PvP kill rows where this sortie is the victim (the killer and the assists), kill first."""
    return list(Kill.objects.filter(victim_sortie=sortie).order_by("tick", "pk"))


def sorties_by_id(ids: Iterable[int]) -> dict[int, PlayerSortie]:
    """The counterpart sorties (any visibility: the caller anonymises hidden players) with player and aircraft."""
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    rows = PlayerSortie.objects.filter(pk__in=wanted).select_related("player", "aircraft")
    return {row.pk: row for row in rows}


def objects_by_log_name(names: Iterable[str]) -> dict[str, GameObject]:
    """Game objects by log name (display name, class, ground category) for AI and ground counterparts."""
    wanted = sorted({name for name in names if name})
    if not wanted:
        return {}
    return {obj.log_name: obj for obj in GameObject.objects.filter(log_name__in=wanted)}
