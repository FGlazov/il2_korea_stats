"""Reads behind the leaderboards (FR-WEB-7, FR-WEB-19, FR-WEB-20). Simple SELECTs only: no aggregation at request time
(TD-22). Scores, kills and time on target are stored counters (`PlayerTour`, `Player`, and their per-aircraft tables);
Elo is stored on `Player`. The only arithmetic is the ground score per hour, a division of two stored columns.

Boards (`BOARDS`): `air`, `ground`, `ground-hour`, `kills`, `elo-prop`, `elo-jet`. Per tour (`?tour=`, TD-26) the rows
are `PlayerTour`, all-time `Player`; with an aircraft chosen (`?aircraft=<GameObject pk>`) they are the per-aircraft
rows `PlayerTourAircraft` / `PlayerAircraft`, so a player appears once per aircraft type. Elo is all-time and has no
aircraft filter (its pools are the split). A minimum of activity (`LeaderboardConfig`) keeps one lucky sortie off it.

Hiding (FR-ADM-3): hidden players are never listed.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from django.core.paginator import Page, Paginator
from django.db.models import ExpressionWrapper, F, FloatField, Model, QuerySet

from il2ks.config import LeaderboardConfig
from il2ks.db.models import GameObject, Player, PlayerAircraft, PlayerTour, PlayerTourAircraft, Tour

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
    ),
    "kills": Board(
        "kills",
        {**_COMMON, "kills_air": "kills_air", "kills_ground": "kills_ground", "deaths": "deaths"},
        "-kills_air",
    ),
    "elo-prop": Board(
        "elo-prop",
        {**_COMMON, "rating": "elo_prop", "games": "elo_prop_games"},
        "-rating",
        per_tour=False,
        per_aircraft=False,
    ),
    "elo-jet": Board(
        "elo-jet",
        {**_COMMON, "rating": "elo_jet", "games": "elo_jet_games"},
        "-rating",
        per_tour=False,
        per_aircraft=False,
    ),
}
DEFAULT_BOARD: Final = "air"


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
    wanted = int(raw) if raw is not None and raw.isdecimal() else None
    return next((a for a in options if a.pk == wanted), None)


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
) -> Page:
    """One page of a board (`sort` resolved by `resolve_sort`; `tour` / `aircraft` ignored where the board has none).
    Rows come from the board's source table, visible players only, with their player. Cost: a COUNT and one SELECT."""
    per_aircraft = aircraft is not None and board.per_aircraft
    if board.per_tour and tour is not None:
        if per_aircraft:
            tour_aircraft = PlayerTourAircraft.objects.filter(tour=tour, aircraft=aircraft, player__is_hidden=False)
            return _page(board, tour_aircraft, sort, page_number, rules)
        return _page(board, PlayerTour.objects.filter(tour=tour, player__is_hidden=False), sort, page_number, rules)
    if per_aircraft:
        all_time = PlayerAircraft.objects.filter(aircraft=aircraft, player__is_hidden=False)
        return _page(board, all_time, sort, page_number, rules)
    return _page(board, Player.objects.filter(is_hidden=False), sort, page_number, rules)


def _page[M: Model](
    board: Board, rows: QuerySet[M], sort: str, page_number: str | int | None, rules: LeaderboardConfig
) -> Page:
    rows = _apply_minimums(board, rows, rules)
    if rows.model is not Player:
        rows = rows.select_related("player")
    prefix = "" if rows.model is Player else "player__"
    column = board.sorts[sort.removeprefix("-")].replace("player__", prefix)
    # The player's name and the row id break ties, so paging is stable.
    ordered = rows.order_by(f"{'-' if sort.startswith('-') else ''}{column}", f"{prefix}name_lower", "pk")
    page = Paginator(ordered, PAGE_SIZE).get_page(page_number)
    offset = (page.number - 1) * PAGE_SIZE
    items = [_row(offset + i + 1, stats) for i, stats in enumerate(page.object_list)]
    return Page(items, page.number, page.paginator)  # pyright: ignore[reportArgumentType]


def _row(rank: int, stats: Model) -> BoardRow:
    per_hour = getattr(stats, "per_hour", None)
    player = stats if isinstance(stats, Player) else getattr(stats, "player")  # noqa: B009
    return BoardRow(rank, player, stats, per_hour if isinstance(per_hour, float) else None)
