"""Reads behind the leaderboards (FR-WEB-7, FR-WEB-19, FR-WEB-20). Simple SELECTs only: no aggregation at request time
(TD-22). Scores, kills and time on target are stored counters (`PlayerTour`, `Player`, and their per-aircraft tables);
Elo is stored on `Player`. The only arithmetic is the ground score per hour, a division of two stored columns.

Boards (`BOARDS`): `air`, `ground`, `ground-hour`, `kills`, `elo-prop`, `elo-jet`, grouped into fighter boards (air
score, Elo), attack boards (ground score, ground score per hour) and the general kills board (`GROUPS`). Per tour
(`?tour=`, TD-26) the rows are `PlayerTour`, all-time `Player`; with an aircraft chosen (`?aircraft=<GameObject pk>`)
they are the per-aircraft rows `PlayerTourAircraft` / `PlayerAircraft`, so a player appears once per aircraft type; with
a pool chosen (`?pool=prop|jet`) they are the per-propulsion rows `PlayerTourPool` / `PlayerPool` (a chosen aircraft
wins over a pool). Elo is all-time and has no aircraft or pool filter (its pools are the split). A minimum of activity
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

PAGE_SIZE = 25
SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True, slots=True)
class Board:
    """One leaderboard: the columns it can be sorted by (public key -> column) and what it supports."""

    key: str
    sorts: Mapping[str, str]
    default_sort: str
    per_tour: bool = True  # False: all-time only (Elo)
    per_aircraft: bool = True
    per_pool: bool = True  # False: the board is a pool already (Elo) or has none
    group: str = "fighter"  # the tab group: "fighter", "attack" or "general"


_COMMON: Final[Mapping[str, str]] = {"sorties": "sorties", "name": "player__name_lower"}

BOARDS: Final[Mapping[str, Board]] = {
    "air": Board(
        "air",
        {**_COMMON, "score": "score_air", "kills_air": "kills_air", "assists": "assists", "deaths": "deaths"},
        "-score",
    ),
    "ground": Board(
        "ground",
        {**_COMMON, "score": "score_ground", "kills_ground": "kills_ground", "attack_sorties": "attack_sorties"},
        "-score",
        group="attack",
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
        group="attack",
    ),
    "kills": Board(
        "kills",
        {**_COMMON, "kills_air": "kills_air", "kills_ground": "kills_ground", "deaths": "deaths"},
        "-kills_air",
        group="general",
    ),
    "elo-prop": Board(
        "elo-prop",
        {**_COMMON, "rating": "elo_prop", "games": "elo_prop_games"},
        "-rating",
        per_tour=False,
        per_aircraft=False,
        per_pool=False,
    ),
    "elo-jet": Board(
        "elo-jet",
        {**_COMMON, "rating": "elo_jet", "games": "elo_jet_games"},
        "-rating",
        per_tour=False,
        per_aircraft=False,
        per_pool=False,
    ),
}
DEFAULT_BOARD: Final = "air"
GROUPS: Final[tuple[str, ...]] = ("fighter", "attack", "general")
POOLS: Final[tuple[str, ...]] = ("prop", "jet")
HOME_BOARDS: Final[tuple[str, ...]] = ("elo-jet", "elo-prop", "ground-hour")
"""The boards the home page highlights (maintainer, OQ-64): Elo of both pools and ground proficiency."""
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
    ground score per hour on target (ground-hour board only)."""

    rank: int
    player: Player
    stats: Model
    per_hour: float | None = None


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
        case "elo-prop":
            return rows.filter(elo_prop_games__gte=rules.min_elo_games)
        case "elo-jet":
            return rows.filter(elo_jet_games__gte=rules.min_elo_games)
        case "ground-hour":
            return rows.filter(
                attack_sorties__gte=max(rules.min_attack_sorties, 1),
                time_on_target_s__gte=max(rules.min_time_on_target_minutes * 60.0, 1.0),
            ).annotate(
                per_hour=ExpressionWrapper(
                    F("score_ground_attack") * SECONDS_PER_HOUR / F("time_on_target_s"), output_field=FloatField()
                )
            )
        case _:
            return rows.filter(sorties__gte=max(rules.min_sorties, 1))


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
    board: Board, rules: LeaderboardConfig, limit: int = HOME_ROWS, aircraft: GameObject | None = None
) -> list[BoardRow]:
    """The first `limit` rows of a board in its default order, all time (the home page's compact boards, the top pilots
    of an aircraft type). One SELECT."""
    rows = _ordered(board, _apply_minimums(board, _source(board, None, aircraft, None), rules), board.default_sort)
    return [_row(i + 1, stats) for i, stats in enumerate(rows[:limit])]


def _source(board: Board, tour: Tour | None, aircraft: GameObject | None, pool: str | None) -> QuerySet[Model]:
    """The table the board reads: the player (all time), a tour, an aircraft type or a propulsion pool."""
    tour = tour if board.per_tour else None
    aircraft = aircraft if board.per_aircraft else None
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
    # The player's name and the row id break ties, so paging is stable.
    return rows.order_by(f"{'-' if sort.startswith('-') else ''}{column}", f"{prefix}name_lower", "pk")


def _page[M: Model](
    board: Board, rows: QuerySet[M], sort: str, page_number: str | int | None, rules: LeaderboardConfig
) -> Page:
    ordered = _ordered(board, _apply_minimums(board, rows, rules), sort)
    page = Paginator(ordered, PAGE_SIZE).get_page(page_number)
    offset = (page.number - 1) * PAGE_SIZE
    items = [_row(offset + i + 1, stats) for i, stats in enumerate(page.object_list)]
    return Page(items, page.number, page.paginator)  # pyright: ignore[reportArgumentType]


def _row(rank: int, stats: Model) -> BoardRow:
    per_hour = getattr(stats, "per_hour", None)
    player = stats if isinstance(stats, Player) else getattr(stats, "player")  # noqa: B009
    return BoardRow(rank, player, stats, per_hour if isinstance(per_hour, float) else None)
