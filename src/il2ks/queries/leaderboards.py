"""Reads behind the leaderboards (FR-WEB-7, FR-WEB-19, FR-WEB-20). Simple SELECTs only: no aggregation at request time
(TD-22). Scores, kills and time on target are stored counters (`PlayerTour`, `Player`, and their per-aircraft tables);
Elo is stored per tour (`PlayerTourPool.elo`, the tour's final rating) and all time on `Player` (the best tour's).
The only arithmetic is the per-hour rate of the skill boards: two stored columns divided.

Boards (`BOARDS`, in tab order): `elo-jet`, `elo-prop`, `air`, `interception`, then `ground-hour`, `tank-busting`,
`ground`, then `play-time`, in an air group, a ground group and a general group (`GROUPS`; maintainer decision
2026-10-04: no kills board). Per tour
(`?tour=`, TD-26) the rows are `PlayerTour`, all-time `Player`; with an aircraft chosen (`?aircraft=<GameObject pk>`)
they are the per-aircraft rows `PlayerTourAircraft` / `PlayerAircraft`, so a player appears once per aircraft type; with
a pool chosen (`?pool=prop|jet`) they are the per-propulsion rows `PlayerTourPool` / `PlayerPool` (a chosen aircraft
wins over a pool). The Elo boards show the tour's rating in a tour (OQ-128: Elo resets at every tour) and the best
tour's rating all time; they have no aircraft or pool filter (their pools are the split). A minimum of activity
(`LeaderboardConfig`) keeps one lucky sortie off it.

Hiding (FR-ADM-3): hidden players are never listed.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, cast

from django.conf import settings
from django.core.paginator import Page, Paginator
from django.db.models import ExpressionWrapper, F, FloatField, Model, QuerySet

from il2ks.config import LeaderboardConfig
from il2ks.db.models import (
    GameObject,
    Player,
    PlayerAircraft,
    PlayerPool,
    PlayerTour,
    PlayerTourAircraft,
    PlayerTourPool,
    Tour,
)
from il2ks.queries.paging import ROW_PAGE_SIZE

PAGE_SIZE = ROW_PAGE_SIZE
SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True, slots=True)
class Board:
    """One leaderboard: the columns it can be sorted by (public key -> column) and what it supports."""

    key: str
    sorts: Mapping[str, str]
    default_sort: str
    per_tour: bool = True
    per_aircraft: bool = True
    per_pool: bool = True  # False: the board is a pool already (Elo) or has none
    group: str = "air"  # the tab group: "air", "ground" or "general"
    elo_pool: str | None = (
        None  # an Elo board: its propulsion pool (rows `Player` all time, `PlayerTourPool` in a tour)
    )


_COMMON: Final[Mapping[str, str]] = {"sorties": "sorties", "name": "player__name_lower"}

BOARDS: Final[Mapping[str, Board]] = {
    "elo-jet": Board(
        "elo-jet",
        {**_COMMON, "rating": "elo_jet", "games": "elo_jet_games"},
        "-rating",
        per_aircraft=False,
        per_pool=False,
        elo_pool="jet",
    ),
    "elo-prop": Board(
        "elo-prop",
        {**_COMMON, "rating": "elo_prop", "games": "elo_prop_games"},
        "-rating",
        per_aircraft=False,
        per_pool=False,
        elo_pool="prop",
    ),
    "air": Board(
        "air",
        {**_COMMON, "score": "score_air", "kills_air": "kills_air", "assists": "assists_air", "deaths": "deaths"},
        "-score",
    ),
    "interception": Board(
        "interception",
        {
            **_COMMON,
            "per_hour": "per_hour",
            "kills": "kills_intercept",
            "flight_time_s": "flight_time_air_s",
            "air_superiority_sorties": "air_superiority_sorties",
        },
        "-per_hour",
    ),
    "ground-hour": Board(
        "ground-hour",
        {
            **_COMMON,
            "per_hour": "per_hour",
            "score": "score_ground_attack",
            "time_on_target_s": "time_on_target_s",
            "attack_sorties": "attack_sorties",
        },
        "-per_hour",
        group="ground",
    ),
    "tank-busting": Board(
        "tank-busting",
        {
            **_COMMON,
            "per_hour": "per_hour",
            "tanks": "kills_tank_attack",
            "time_on_target_s": "time_on_target_s",
            "attack_sorties": "attack_sorties",
        },
        "-per_hour",
        group="ground",
    ),
    "ground": Board(
        "ground",
        {**_COMMON, "score": "score_ground", "kills_ground": "kills_ground", "attack_sorties": "attack_sorties"},
        "-score",
        group="ground",
    ),
    "play-time": Board(
        "play-time",
        {**_COMMON, "flight_time_s": "flight_time_s"},
        "-flight_time_s",
        group="general",
    ),
}
DEFAULT_BOARD: Final = "air"
GROUPS: Final[tuple[str, ...]] = ("air", "ground", "general")
POOLS: Final[tuple[str, ...]] = ("prop", "jet")
HOME_BOARDS: Final[tuple[str, ...]] = (
    "elo-jet",
    "elo-prop",
    "interception",
    "ground-hour",
    "tank-busting",
    "play-time",
)
"""The boards the home page highlights (maintainer, OQ-64, OQ-79): Elo of both pools, the skill boards (interception,
ground score per hour, tank busting) and play time, laid out as a 3x2 grid."""
HOME_ROWS = 5
MAX_PK_DIGITS = 18  # int() of a longer digit string is slow or raises (4300-digit limit): not a pk


def rules() -> LeaderboardConfig:
    """The minimum-activity thresholds (`[score]`)."""
    configured = getattr(settings, "IL2KS_LEADERBOARDS", None)
    return configured if isinstance(configured, LeaderboardConfig) else LeaderboardConfig()


@dataclass(frozen=True, slots=True)
class BoardRow:
    """A row of a leaderboard: `rank` follows the current order; `stats` holds the counters (it is the `Player` itself
    for all-time boards, a `PlayerTour` / `PlayerAircraft` / `PlayerTourAircraft` row otherwise); `per_hour` is the
    ground score per hour on target (ground-hour board only); `rating` / `games` are the Elo and encounters of an Elo
    board, whichever table the row comes from."""

    rank: int
    player: Player
    stats: Model
    per_hour: float | None = None
    rating: float | None = None  # Elo boards only: the Elo shown (tour rating, or the best tour's all time)
    games: int | None = None  # Elo boards only: the encounters behind it


def resolve_sort(board: Board, raw: str) -> str:
    """The `?sort=` value to use: 'score' or '-score' if the board has that column, else its default."""
    return raw if raw.removeprefix("-") in board.sorts else board.default_sort


def aircraft_options() -> list[GameObject]:
    """The playable aircraft types for the aircraft filter (one read; the view turns them into option labels)."""
    return list(GameObject.objects.filter(is_playable=True).order_by("display_name"))


def parse_aircraft(raw: str | None, options: list[GameObject]) -> GameObject | None:
    """`?aircraft=<pk>` as one of the offered aircraft; an unknown or malformed value means all aircraft."""
    wanted = int(raw) if raw is not None and raw.isdecimal() and len(raw) <= MAX_PK_DIGITS else None
    return next((a for a in options if a.pk == wanted), None)


def parse_pool(raw: str | None, board: Board) -> str | None:
    """`?pool=prop|jet`; anything else (or a board without the split) means both pools."""
    return raw if board.per_pool and raw in POOLS else None


def _apply_minimums[M: Model](board: Board, rows: QuerySet[M], rules: LeaderboardConfig) -> QuerySet[M]:
    """Keep rows with enough activity for this board (the thresholds of `[score]`)."""
    match board.key:
        case "elo-prop" | "elo-jet":
            return rows.filter(**{f"{_elo_columns(board, rows.model)[1]}__gte": rules.min_elo_games})
        case "play-time":
            return rows.filter(flight_time_s__gt=0)  # no sortie minimum: the board is hours flown
        case "ground-hour" | "tank-busting":
            return _per_hour(
                rows.filter(
                    attack_sorties__gte=max(rules.min_attack_sorties, 1),
                    time_on_target_s__gte=max(rules.min_time_on_target_minutes * 60.0, 1.0),
                ),
                "score_ground_attack" if board.key == "ground-hour" else "kills_tank_attack",
                "time_on_target_s",
            )
        case "interception":
            return _per_hour(
                rows.filter(
                    air_superiority_sorties__gte=max(rules.min_air_superiority_sorties, 1),
                    flight_time_air_s__gte=max(rules.min_air_superiority_minutes * 60.0, 1.0),
                ),
                "kills_intercept",
                "flight_time_air_s",
            )
        case _:
            return rows.filter(sorties__gte=max(rules.min_sorties, 1))


def _elo_columns(board: Board, model: type[Model]) -> tuple[str, str]:
    """The (rating, games) columns of an Elo board's rows: one pair per pool on `Player`, `elo` / `elo_games` on the
    pool rows of a tour."""
    if model is PlayerTourPool:
        return "elo", "elo_games"
    return f"elo_{board.elo_pool}", f"elo_{board.elo_pool}_games"


def _per_hour[M: Model](rows: QuerySet[M], amount: str, seconds: str) -> QuerySet[M]:
    """Annotate `per_hour` = `amount` per hour of `seconds` (a division of two stored columns; the rows already passed a
    minimum above 0 seconds)."""
    return rows.annotate(
        per_hour=ExpressionWrapper(F(amount) * SECONDS_PER_HOUR / F(seconds), output_field=FloatField())
    )


def board_page(
    board: Board,
    sort: str,
    page_number: str | int | None,
    rules: LeaderboardConfig,
    tour: Tour | None = None,
    aircraft: GameObject | None = None,
    pool: str | None = None,
) -> Page:
    """One page of a board (`sort` resolved by `resolve_sort`; `tour` / `aircraft` / `pool` ignored where the board has
    none). Rows come from the board's source table, visible players only, with their player. Cost: a COUNT and one
    SELECT."""
    return _page(board, _source(board, tour, aircraft, pool), sort, page_number, rules)


def top_rows(
    board: Board,
    rules: LeaderboardConfig,
    limit: int = HOME_ROWS,
    aircraft: GameObject | None = None,
    tour: Tour | None = None,
) -> list[BoardRow]:
    """The first `limit` rows of a board in its default order, all time or in `tour` (ignored by the all-time Elo
    boards): the home page's compact boards, the top pilots of an aircraft type. One SELECT."""
    rows = _ordered(board, _apply_minimums(board, _source(board, tour, aircraft, None), rules), board.default_sort)
    return [_row(i + 1, stats, board) for i, stats in enumerate(rows[:limit])]


def _source(board: Board, tour: Tour | None, aircraft: GameObject | None, pool: str | None) -> QuerySet[Model]:
    """The table the board reads: the player (all time), a tour, an aircraft type or a propulsion pool."""
    tour = tour if board.per_tour else None
    aircraft = aircraft if board.per_aircraft else None
    if board.elo_pool is not None:
        if tour is not None:
            return _rows(PlayerTourPool.objects.filter(tour=tour, propulsion=board.elo_pool, player__is_hidden=False))
        return _rows(Player.objects.filter(is_hidden=False))
    pool = pool if board.per_pool and aircraft is None else None
    if aircraft is not None:
        if tour is not None:
            return _rows(PlayerTourAircraft.objects.filter(tour=tour, aircraft=aircraft, player__is_hidden=False))
        return _rows(PlayerAircraft.objects.filter(aircraft=aircraft, player__is_hidden=False))
    if pool is not None:
        if tour is not None:
            return _rows(PlayerTourPool.objects.filter(tour=tour, propulsion=pool, player__is_hidden=False))
        return _rows(PlayerPool.objects.filter(propulsion=pool, player__is_hidden=False))
    if tour is not None:
        return _rows(PlayerTour.objects.filter(tour=tour, player__is_hidden=False))
    return _rows(Player.objects.filter(is_hidden=False))


def _rows[M: Model](rows: QuerySet[M]) -> QuerySet[Model]:
    """A board's rows seen as plain model rows (the boards share their column names, not a base class)."""
    return cast("QuerySet[Model]", rows)


def _ordered[M: Model](board: Board, rows: QuerySet[M], sort: str) -> QuerySet[M]:
    if rows.model is not Player:
        rows = rows.select_related("player")
    prefix = "" if rows.model is Player else "player__"
    column = board.sorts[sort.removeprefix("-")].replace("player__", prefix)
    if board.elo_pool is not None and rows.model is PlayerTourPool:  # the same public keys, the tour table's columns
        rating, games = _elo_columns(board, rows.model)
        column = {f"elo_{board.elo_pool}": rating, f"elo_{board.elo_pool}_games": games}.get(column, column)
    # The player's name and the row id break ties, so paging is stable.
    return rows.order_by(f"{'-' if sort.startswith('-') else ''}{column}", f"{prefix}name_lower", "pk")


def _page[M: Model](
    board: Board, rows: QuerySet[M], sort: str, page_number: str | int | None, rules: LeaderboardConfig
) -> Page:
    ordered = _ordered(board, _apply_minimums(board, rows, rules), sort)
    page = Paginator(ordered, PAGE_SIZE).get_page(page_number)
    offset = (page.number - 1) * PAGE_SIZE
    items = [_row(offset + i + 1, stats, board) for i, stats in enumerate(page.object_list)]
    return Page(items, page.number, page.paginator)  # pyright: ignore[reportArgumentType]


def _row(rank: int, stats: Model, board: Board) -> BoardRow:
    per_hour = getattr(stats, "per_hour", None)
    player = stats if isinstance(stats, Player) else getattr(stats, "player")  # noqa: B009
    rating = games = None
    if board.elo_pool is not None:
        rating_column, games_column = _elo_columns(board, type(stats))
        rating, games = getattr(stats, rating_column), getattr(stats, games_column)
    return BoardRow(rank, player, stats, per_hour if isinstance(per_hour, float) else None, rating, games)
