"""Reads behind the killboard and the ironman streaks (FR-WEB-9, FR-WEB-23). Simple SELECTs only (TD-22).

They read the level-2 tables `PlayerKillboard` / `PlayerTourKillboard`, `PlayerStreak` and `PlayerBestStreak`
(`ingest.pairs`, `ingest.streaks`); a `tour` argument switches to the per-tour rows, same shape. Hiding
(FR-ADM-3): the callers pass a visible player; an opponent who is hidden comes back like any other row and the
templates show "Hidden player" without a link. Streak lists leave hidden players out. Hidden missions count in the
numbers (hiding is presentation only), but the templates don't link them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.paginator import Page, Paginator
from django.db.models import QuerySet

from il2ks.db.models import (
    HEAVY_SORTIE_COLUMNS,
    Player,
    PlayerBestStreak,
    PlayerKillboard,
    PlayerStreak,
    PlayerStreakRun,
    PlayerTourKillboard,
    PlayerTypeKillboard,
    StreakKind,
    StreakTrack,
    Tour,
)
from il2ks.queries.paging import ROW_PAGE_SIZE

PAGE_SIZE = ROW_PAGE_SIZE
RUNS_PAGE_SIZE = ROW_PAGE_SIZE  # streak runs per page (OQ-82)
TOP_OPPONENTS = 5
TOP_TYPES = 5  # enemy aircraft types per direction in the profile block
ACTIVE_DAYS = 30  # a "current" streak is listed while the player's last flown sortie is at most this old

KILLBOARD_SORTS: Mapping[str, str] = {
    "opponent": "opponent__name_lower",
    "kills": "kills",
    "deaths": "deaths",
    "assists": "assists",
    "last": "last_at",
}
DEFAULT_KILLBOARD_SORT = "-kills"


def _order(sort: str, allowed: Mapping[str, str]) -> str:
    return f"{'-' if sort.startswith('-') else ''}{allowed[sort.removeprefix('-')]}"


def _board_rows(player: Player, tour: Tour | None) -> QuerySet[PlayerKillboard] | QuerySet[PlayerTourKillboard]:
    """The player's killboard rows, all-time or in `tour`, opponent and last mission pre-loaded."""
    rows = (
        PlayerKillboard.objects.filter(player=player)
        if tour is None
        else PlayerTourKillboard.objects.filter(player=player, tour=tour)
    )
    return rows.select_related("opponent", "last_mission")


def top_victims(
    player: Player, tour: Tour | None = None, limit: int = TOP_OPPONENTS
) -> Sequence[PlayerKillboard | PlayerTourKillboard]:
    """Whom the player shot down most: opponents with a kill, most kills first (latest encounter breaks ties)."""
    return list(_board_rows(player, tour).filter(kills__gt=0).order_by("-kills", "-last_at", "pk")[:limit])


def top_nemeses(
    player: Player, tour: Tour | None = None, limit: int = TOP_OPPONENTS
) -> Sequence[PlayerKillboard | PlayerTourKillboard]:
    """Who shot the player down most: opponents with at least one kill on the player, most first."""
    return list(_board_rows(player, tour).filter(deaths__gt=0).order_by("-deaths", "-last_at", "pk")[:limit])


@dataclass(frozen=True, slots=True)
class TypeBoard:
    """A player's killboard by aircraft type: enemy types shot down most and enemy types that killed the player most."""

    victims: Sequence[PlayerTypeKillboard]  # kills > 0, most first
    nemeses: Sequence[PlayerTypeKillboard]  # deaths > 0, most first


def type_board(player: Player, tour: Tour | None = None, limit: int = TOP_TYPES) -> TypeBoard:
    """The enemy aircraft types the player shot down most and the types that shot the player down most (all time, or
    in `tour`), at most `limit` each. One SELECT of the player's rows (a few dozen: one per enemy type met), ordered
    here; ties by type name."""
    rows = list(
        PlayerTypeKillboard.objects.filter(player=player, tour=tour).select_related(
            "enemy_aircraft", "kills_with", "deaths_in"
        )
    )

    def top(count: str) -> list[PlayerTypeKillboard]:
        counted = [r for r in rows if getattr(r, count) > 0]
        counted.sort(key=lambda r: (-getattr(r, count), r.enemy_aircraft.display_name, r.pk))
        return counted[:limit]

    return TypeBoard(top("kills"), top("deaths"))


def killboard_page(player: Player, sort: str, number: str | int, tour: Tour | None = None) -> Page:
    """One page of the player's full killboard (every opponent): `sort` is a resolved `KILLBOARD_SORTS` value."""
    rows = _board_rows(player, tour)
    order = [_order(sort, KILLBOARD_SORTS), "pk"]
    if sort.removeprefix("-") == "opponent":
        order.insert(
            0, "opponent__is_hidden"
        )  # hidden opponents last either way: their name must not show in the order
    return Paginator(rows.order_by(*order), PAGE_SIZE).get_page(number)


TRACKS: tuple[str, ...] = tuple(
    StreakTrack.values
)  # all first, then air, then ground (the order every page shows them in)


def streaks_of(player: Player) -> list[PlayerStreak]:
    """The player's current and best ironman run on each track (a row per track with a survived sortie), the all track
    first. One query."""
    return sorted(PlayerStreak.objects.filter(player=player), key=lambda row: TRACKS.index(row.track))


def tour_streaks_of(player: Player, tour: Tour) -> list[PlayerBestStreak]:
    """The player's longest run (by sorties) of each track inside `tour`, the all track first; a track without a
    survived sortie there is missing. One query."""
    rows = PlayerBestStreak.objects.filter(player=player, tour=tour, kind=StreakKind.SORTIES)
    return sorted(rows, key=lambda row: TRACKS.index(row.track))


def best_streaks(player: Player, tour: Tour | None = None) -> list[PlayerBestStreak]:
    """The player's best streaks of the three tracks, each by sorties, kills (air kills on the air track, ground kills
    on the ground track, both on the all track) and flight time, all-time or in `tour`: the all track first, the kinds
    in that order. A kind is missing when it doesn't exist (no kill in any streak of the track)."""
    rows = (
        PlayerBestStreak.objects.filter(player=player, tour=tour)
        if tour
        else PlayerBestStreak.objects.filter(player=player, tour__isnull=True)
    )
    order = {str(kind): n for n, kind in enumerate(StreakKind.values)}
    return sorted(rows, key=lambda row: (TRACKS.index(row.track), order[row.kind]))


def streak_runs_page(player: Player, number: str | int, tour: Tour | None = None, track: str = "air") -> Page:
    """One page of the player's streak runs of one `track` (OQ-82), newest first, all-time or within `tour`; the sortie
    that ended each run is pre-loaded with its mission (the template links it unless the mission is hidden)."""
    rows = (
        PlayerStreakRun.objects.filter(player=player, tour=tour, track=track)
        if tour
        else PlayerStreakRun.objects.filter(player=player, tour__isnull=True, track=track)
    )
    ordered = (
        rows.select_related("ended_sortie__mission")
        .defer(*(f"ended_sortie__{column}" for column in HEAVY_SORTIE_COLUMNS))
        .order_by("-since", "-pk")
    )
    return Paginator(ordered, RUNS_PAGE_SIZE).get_page(number)


def _running(now: datetime, track: str = "air") -> QuerySet[PlayerStreak]:
    """Visible players with a non-empty current streak on `track` whose last sortie ended within `ACTIVE_DAYS` of `now`
    (an old player's streak is history, not "current")."""
    return PlayerStreak.objects.filter(
        track=track,
        current_sorties__gt=0,
        current_until__gte=now - timedelta(days=ACTIVE_DAYS),
        player__is_hidden=False,
    ).select_related("player")


def running_page(track: str, number: str | int, now: datetime) -> Page:
    """One page of the streaks running right now on `track`, longest first (the ironman boards' second table)."""
    # Ties: the track's own kills first (the all track: air, then ground).
    kills = (
        ("-current_kills_ground", "-current_kills_air")
        if track == StreakTrack.GROUND
        else ("-current_kills_air", "-current_kills_ground")
    )
    ordered = _running(now, track).order_by("-current_sorties", *kills, "pk")
    return Paginator(ordered, PAGE_SIZE).get_page(number)
