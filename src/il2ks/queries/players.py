"""Reads behind the player pages (FR-WEB-3, FR-WEB-4). Simple SELECTs only: no aggregation at request time (TD-22).

Hiding (FR-ADM-3): every read starts from `Player.objects.visible()`, so a hidden player is neither listed, found by
search nor reachable by URL. Ratios are not computed here; the templates derive them from the stored counters.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from django.core.paginator import Page, Paginator
from django.db.models import Expression, OrderBy

from il2ks.db.models import (
    HEAVY_SORTIE_COLUMNS,
    Player,
    PlayerAircraft,
    PlayerName,
    PlayerSortie,
    PlayerTour,
    PlayerTourAircraft,
    Role,
    Tour,
)
from il2ks.queries.sorting import Rated, Ratio, SortSpec, order_by
from il2ks.queries.tours import player_tour_aircraft

PAGE_SIZE = 50
RECENT_SORTIES = 10
TOUR_HISTORY = 12  # tours shown in the profile charts
MAX_QUERY_LENGTH = 64

# Public `?sort=` key -> what it orders by (a Player column or a NULL-safe ratio, see `queries.sorting`). Anything else
# falls back to the default (a whitelist). The first block is the default columns, the second the optional ones a
# visitor can add with `?cols=` (`web.columns.PLAYER_COLUMNS`; a test keeps the two in step).
PLAYER_SORTS: Mapping[str, SortSpec] = {
    "name": "name_lower",
    "sorties": "sorties",
    "flight_time_s": "flight_time_s",
    "kills_air": "kills_air",
    "kills_ground": "kills_ground",
    "deaths": "deaths",
    "last_seen": "last_seen",
    "elo_jet": Rated("elo_jet", "elo_jet_games"),
    "elo_prop": Rated("elo_prop", "elo_prop_games"),
    "kd": Ratio("kills_air", "deaths"),
    "kl": Ratio("kills_air", "planes_lost"),
    "survival": Ratio("sorties", "sorties", minus="deaths"),
    "kills_air_pvp": "kills_air_pvp",
    "score_air": "score_air",
    "score_ground": "score_ground",
    "ground_hour": Ratio("score_ground_attack", "time_on_target_s", scale=3600.0),
    "planes_lost": "planes_lost",
    "assists": "assists",
    "friendly_kills": "friendly_kills",
    "first_seen": "first_seen",
}
DEFAULT_PLAYER_SORT = "-last_seen"

# The same for the per-aircraft table on the profile (PlayerAircraft columns, the aircraft by its display name).
AIRCRAFT_SORTS: Mapping[str, SortSpec] = {
    "aircraft": "aircraft__display_name",
    "sorties": "sorties",
    "flight_time_s": "flight_time_s",
    "kills_air": "kills_air",
    "kills_ground": "kills_ground",
    "deaths": "deaths",
}
DEFAULT_AIRCRAFT_SORT = "-sorties"


def resolve_sort(raw: str, allowed: Mapping[str, SortSpec], default: str) -> str:
    """The `?sort=` value to use and to hand back to the sort headers: 'kills_air' or '-kills_air', else `default`."""
    return raw if raw.removeprefix("-") in allowed else default


def _order(sort: str, allowed: Mapping[str, SortSpec], prefix: str = "") -> Expression | OrderBy:
    return order_by(allowed[sort.removeprefix("-")], sort, prefix)


@dataclass(frozen=True, slots=True)
class PlayerHit:
    """One row of the player list. `matched_name` is the old name that matched the search ('' when the current name
    matched, or when nothing was searched), shown as "also known as"."""

    player: Player
    matched_name: str


def _page_of(items: list[PlayerHit], page: Page) -> Page:
    return Page(items, page.number, page.paginator)  # pyright: ignore[reportArgumentType]


def player_page(query: str, sort: str, page_number: str | int) -> Page:
    """One page of visible players: those whose current or any past name contains `query` (case-insensitive), or the
    most recently active ones for an empty query. `sort` is a resolved value (see `resolve_sort`).

    A search lists one row per matching name row, so a player whose old and new names both match can appear twice in
    the count; adjacent duplicates are merged on the page (they sort together). Cost: a COUNT and one SELECT."""
    query = query.strip()[:MAX_QUERY_LENGTH].lower()
    if not query:
        players = Player.objects.visible().order_by(_order(sort, PLAYER_SORTS), "pk")
        page = Paginator(players, PAGE_SIZE).get_page(page_number)
        return _page_of([PlayerHit(player, "") for player in page.object_list], page)
    names = (
        PlayerName.objects.filter(name_lower__contains=query, player__is_hidden=False)
        .select_related("player")
        .order_by(_order(sort, PLAYER_SORTS, "player__"), "player_id")
    )
    page = Paginator(names, PAGE_SIZE).get_page(page_number)
    hits: list[PlayerHit] = []
    for row in page.object_list:
        alias = "" if row.name == row.player.current_name else row.name
        if hits and hits[-1].player.pk == row.player_id:
            if not alias:  # matched under the current name too: no alias needed
                hits[-1] = PlayerHit(hits[-1].player, "")
            continue
        hits.append(PlayerHit(row.player, alias))
    return _page_of(hits, page)


def visible_player(pk: int) -> Player | None:
    return Player.objects.visible().filter(pk=pk).first()


def past_names(player: Player) -> list[PlayerName]:
    """Every name the player has used, newest first (includes the current one)."""
    return list(PlayerName.objects.filter(player=player).order_by("-last_seen", "name_lower"))


def aircraft_rows(player: Player, sort: str, tour: Tour | None = None) -> Sequence[PlayerAircraft | PlayerTourAircraft]:
    """The per-aircraft table: one row per aircraft type the player has flown as pilot, all-time or in `tour`."""
    rows = (
        PlayerAircraft.objects.filter(player=player).select_related("aircraft")
        if tour is None
        else player_tour_aircraft(player.pk, tour)
    )
    return list(rows.order_by(_order(sort, AIRCRAFT_SORTS), "pk"))


def recent_sorties(player: Player, limit: int = RECENT_SORTIES, tour: Tour | None = None) -> list[PlayerSortie]:
    """The latest sorties by spawn time, any role (of `tour` when given); mission and aircraft come along in the same
    query. Sorties of hidden missions are left out (FR-ADM-3)."""
    rows = PlayerSortie.objects.filter(player=player, mission__is_hidden=False).select_related("mission", "aircraft")
    rows = rows.defer(*HEAVY_SORTIE_COLUMNS)
    if tour is not None:
        rows = rows.filter(mission__tour=tour)
    return list(rows.order_by("-spawned_at", "-pk")[:limit])


def flies_as_gunner_only(player: Player) -> bool:
    """True for a player with no pilot sortie who has gunner sorties (counters stay 0 until gunner stats exist)."""
    return (
        player.sorties == 0
        and PlayerSortie.objects.filter(player=player, role=Role.GUNNER, mission__is_hidden=False).exists()
    )


def tour_history(player: Player, limit: int = TOUR_HISTORY) -> list[PlayerTour]:
    """The player's per-tour counters for the profile charts (FR-WEB-16), oldest first, at most the latest `limit`
    tours; the tour comes along in the same query."""
    rows = PlayerTour.objects.filter(player=player).select_related("tour").order_by("-tour__started_at")[:limit]
    return list(reversed(rows))
