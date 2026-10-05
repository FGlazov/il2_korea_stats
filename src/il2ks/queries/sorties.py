"""Reads for the sortie pages (FR-WEB-5, FR-WEB-6, FR-WEB-29): simple SELECTs with simple joins only (TD-22).

Hiding (FR-ADM-3): a sortie of a hidden player or of a hidden mission does not exist for the public pages
(`visible_sortie` answers None, the sortie lists leave hidden missions, and the site-wide list hidden players, out).
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from django.core.paginator import Page, Paginator
from django.db.models import Case, ExpressionWrapper, F, FloatField, QuerySet, When
from django.db.models.expressions import Combinable, Expression

from il2ks.db.models import (
    HEAVY_SORTIE_COLUMNS,
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
from il2ks.queries.paging import ROW_PAGE_SIZE
from il2ks.queries.sorting import Computed, Rated, SortSpec, order_by

PAGE_SIZE = ROW_PAGE_SIZE


def _ratio(prefix: str, hits: Combinable, role: CombatRole | None) -> Expression:
    """`hits` per round fired of a sortie (restricted to one combat role, or any); NULL where the rounds are unknown or
    zero, or the sortie has another role (design_doc/13 "Accuracy": the same sorties feed numerator and denominator)."""
    conditions: dict[str, object] = {f"{prefix}rounds_fired__gt": 0}
    if role is not None:
        conditions[f"{prefix}combat_role"] = role
    return Case(
        When(
            **conditions,
            then=ExpressionWrapper(hits * 1.0 / F(prefix + "rounds_fired"), output_field=FloatField()),
        ),
        default=None,
        output_field=FloatField(),
    )


def _accuracy(prefix: str) -> Expression:
    """Gun hits per round fired of a sortie; NULL where the rounds are unknown or zero."""
    return _ratio(prefix, F(prefix + "gun_hits_air") + F(prefix + "gun_hits_ground"), None)


def _accuracy_air(prefix: str) -> Expression:
    """Hits on aircraft per round fired, air-superiority sorties only; NULL otherwise."""
    return _ratio(prefix, F(prefix + "gun_hits_air"), CombatRole.AIR_SUPERIORITY)


def _accuracy_ground(prefix: str) -> Expression:
    """Hits on ground targets per round fired, attack sorties only; NULL otherwise."""
    return _ratio(prefix, F(prefix + "gun_hits_ground"), CombatRole.ATTACK)


# `?sort=` whitelist: public field name -> ORM ordering. Ties are broken by the primary key, so paging is stable.
SORT_FIELDS: dict[str, SortSpec] = {
    "date": "spawned_at",
    "mission": "mission__started_at",
    "aircraft": "aircraft__display_name",
    "outcome": "outcome",
    "kills_air": "kills_air",
    "kills_ground": "kills_ground",
    "assists": "assists",
    "assists_air": "assists_air",
    "assists_ground": "assists_ground",
    "flight_time": "flight_time_s",
    "damage_taken": "damage_taken",
    # the optional columns (`?cols=`, `web.columns.SORTIE_COLUMNS`; a test keeps the two in step)
    "kills_air_pvp": "kills_air_pvp",
    "kills_air_ai": "kills_air_ai",
    "friendly_kills": "friendly_kills",
    "air_points": "air_points",
    "ground_points": "ground_points",
    "time_on_target": Rated("time_on_target_s", "time_on_target_s"),  # NULL / 0 (not an attack sortie) sorts last
    "payload": "payload_name",
    "takeoffs": "takeoffs",
    "landings": "landings",
    "accuracy": Computed(_accuracy),  # NULL (rounds fired unknown or none) sorts last
    "accuracy_air": Computed(_accuracy_air),  # NULL (not an air-superiority sortie, or no rounds known) sorts last
    "accuracy_ground": Computed(_accuracy_ground),  # NULL (not an attack sortie, or no rounds known) sorts last
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
    pilot: str = ""
    """The site-wide list's pilot search: the name the pilot flew under (`name_at_time`) contains it, any case."""


def parse_filters(raw: dict[str, str], aircraft_ids: Collection[int]) -> SortieFilters:
    """Filters from raw query values; a value that is not one of the offered choices is ignored, never an error."""
    aircraft = int(raw["aircraft"]) if raw.get("aircraft", "").isdecimal() else None
    return SortieFilters(
        aircraft=aircraft if aircraft in aircraft_ids else None,
        outcome=raw.get("outcome", "") if raw.get("outcome", "") in Outcome.values else "",
        role=raw.get("role", "") if raw.get("role", "") in Role.values else "",
        combat_role=raw.get("combat_role", "") if raw.get("combat_role", "") in CombatRole.values else "",
    )


SEAT_ANY = "any"
"""`?seat=any` on the site-wide list: pilot and gunner sorties together (the default is pilot sorties only, like the
statistics, `?seat=gunner` the gunners)."""
MAX_PILOT_QUERY = 64


def parse_seat(raw: str) -> str:
    """The site-wide list's seat filter as a `PlayerSortie.role` value ('' = any seat): no parameter = pilots only,
    `gunner` = gunners, `any` = both; an unknown value is the default."""
    if raw == Role.GUNNER.value:
        return Role.GUNNER.value
    return "" if raw == SEAT_ANY else Role.PILOT.value


def parse_pilot(raw: str) -> str:
    """The pilot search text: trimmed, at most `MAX_PILOT_QUERY` characters."""
    return raw.strip()[:MAX_PILOT_QUERY]


def player_aircraft(player: Player) -> list[PlayerAircraft]:
    """The aircraft types a player has flown (the filter's choices), by name."""
    rows = PlayerAircraft.objects.filter(player=player).select_related("aircraft")
    return list(rows.order_by("aircraft__display_name", "aircraft_id"))


def _page(rows: QuerySet[PlayerSortie], filters: SortieFilters, sort: str, number: str) -> Page:
    """The shared part of the sortie lists: the filters, the sort (ties by pk, so paging is stable) and the page."""
    rows = rows.defer(*HEAVY_SORTIE_COLUMNS)
    if filters.tour is not None:
        rows = rows.filter(tour=filters.tour)
    if filters.aircraft is not None:
        rows = rows.filter(aircraft_id=filters.aircraft)
    if filters.outcome:
        rows = rows.filter(outcome=filters.outcome)
    if filters.role:
        rows = rows.filter(role=filters.role)
    if filters.combat_role:
        rows = rows.filter(combat_role=filters.combat_role)
    if filters.pilot:
        rows = rows.filter(name_at_time__icontains=filters.pilot)
    rows = rows.order_by(order_by(SORT_FIELDS[sort.removeprefix("-")], sort), "-pk" if sort.startswith("-") else "pk")
    return Paginator(rows, PAGE_SIZE).get_page(number)


def sortie_page(player: Player, filters: SortieFilters, sort: str, number: str) -> Page:
    """One page of a player's sorties in visible missions: filtered, sorted (`sort` already resolved) and paginated."""
    rows = PlayerSortie.objects.filter(player=player, mission__is_hidden=False).select_related("mission", "aircraft")
    return _page(rows, filters, sort, number)


def all_sortie_page(filters: SortieFilters, sort: str, number: str) -> Page:
    """One page of every sortie of the site (`/sorties/`): the visible players' sorties in visible missions, with the
    pilot, mission and aircraft of each row, filtered, sorted (`sort` already resolved) and paginated. `filters.role`
    is the seat ('' = both, see `parse_seat`)."""
    rows = PlayerSortie.objects.filter(mission__is_hidden=False, player__is_hidden=False).select_related(
        "player", "mission", "aircraft"
    )
    return _page(rows, filters, sort, number)


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
    rows = PlayerSortie.objects.filter(pk__in=wanted).select_related("player", "aircraft").defer(*HEAVY_SORTIE_COLUMNS)
    return {row.pk: row for row in rows}


def objects_by_log_name(names: Iterable[str]) -> dict[str, GameObject]:
    """Game objects by log name (display name, class, ground category) for AI and ground counterparts."""
    wanted = sorted({name for name in names if name})
    if not wanted:
        return {}
    return {obj.log_name: obj for obj in GameObject.objects.filter(log_name__in=wanted)}
