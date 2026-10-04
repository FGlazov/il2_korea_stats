"""Reads behind the killboard and the ironman streaks (FR-WEB-9, FR-WEB-23). Simple SELECTs only (TD-22).

Both read the level-2 tables `PlayerKillboard` and `PlayerStreak` (`ingest.pairs`, `ingest.streaks`). Hiding
(FR-ADM-3): the callers pass a visible player; an opponent who is hidden comes back like any other row and the
templates show "Hidden player" without a link. Streak lists leave hidden players out. Hidden missions count in the
numbers (hiding is presentation only), but the templates don't link them.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta

from django.core.paginator import Page, Paginator
from django.db.models import QuerySet

from il2ks.db.models import Player, PlayerKillboard, PlayerStreak

PAGE_SIZE = 50
TOP_OPPONENTS = 5
HOME_STREAKS = 5
ACTIVE_DAYS = 30  # a "current" streak is listed while the player's last flown sortie is at most this old

KILLBOARD_SORTS: Mapping[str, str] = {
    "opponent": "opponent__name_lower",
    "kills": "kills",
    "deaths": "deaths",
    "last": "last_at",
}
DEFAULT_KILLBOARD_SORT = "-kills"

STREAK_SORTS: Mapping[str, str] = {
    "player": "player__name_lower",
    "current": "current_sorties",
    "current_kills": "current_kills_air",
    "current_time": "current_flight_time_s",
    "best": "best_sorties",
    "best_kills": "best_kills_air",
}
DEFAULT_STREAK_SORT = "-current"


def _order(sort: str, allowed: Mapping[str, str]) -> str:
    return f"{'-' if sort.startswith('-') else ''}{allowed[sort.removeprefix('-')]}"


def top_victims(player: Player, limit: int = TOP_OPPONENTS) -> list[PlayerKillboard]:
    """Whom the player shot down most: opponents with a kill, most kills first (latest encounter breaks ties)."""
    rows = PlayerKillboard.objects.filter(player=player, kills__gt=0).select_related("opponent", "last_mission")
    return list(rows.order_by("-kills", "-last_at", "pk")[:limit])


def top_nemeses(player: Player, limit: int = TOP_OPPONENTS) -> list[PlayerKillboard]:
    """Who shot the player down most: opponents with at least one kill on the player, most first."""
    rows = PlayerKillboard.objects.filter(player=player, deaths__gt=0).select_related("opponent", "last_mission")
    return list(rows.order_by("-deaths", "-last_at", "pk")[:limit])


def killboard_page(player: Player, sort: str, number: str | int) -> Page:
    """One page of the player's full killboard (every opponent): `sort` is a resolved `KILLBOARD_SORTS` value."""
    rows = PlayerKillboard.objects.filter(player=player).select_related("opponent", "last_mission")
    return Paginator(rows.order_by(_order(sort, KILLBOARD_SORTS), "pk"), PAGE_SIZE).get_page(number)


def streak_of(player: Player) -> PlayerStreak | None:
    return PlayerStreak.objects.filter(player=player).first()


def _running(now: datetime) -> QuerySet[PlayerStreak]:
    """Visible players with a non-empty current streak whose last sortie ended within `ACTIVE_DAYS` of `now` (an old
    player's streak is history, not "current")."""
    return PlayerStreak.objects.filter(
        current_sorties__gt=0, current_until__gte=now - timedelta(days=ACTIVE_DAYS), player__is_hidden=False
    ).select_related("player")


def streak_page(sort: str, number: str | int, now: datetime) -> Page:
    """One page of the running streaks, `sort` a resolved `STREAK_SORTS` value."""
    return Paginator(_running(now).order_by(_order(sort, STREAK_SORTS), "pk"), PAGE_SIZE).get_page(number)


def longest_current_streaks(now: datetime, limit: int = HOME_STREAKS) -> list[PlayerStreak]:
    """The home page's short list: the longest running streaks."""
    return list(_running(now).order_by("-current_sorties", "-current_kills_air", "pk")[:limit])
