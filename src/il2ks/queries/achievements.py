"""Reads behind the medals (FR-WEB-26, doc 17). Simple SELECTs only (TD-22).

They read the level-2 tables `PlayerAchievement` and `AchievementHolders` (`ingest.achievements`). Every read is for
one **scope**: a `Tour` or None for all time (the page's tour choice, `queries.tours`). Hiding (FR-ADM-3): callers pass
a visible player; the holder lists, the counts and the feed leave hidden players out. A medal's sortie is linked only
while its mission is visible (the templates check `mission_hidden`).
"""

from collections.abc import Iterable
from typing import NamedTuple

from django.core.paginator import Page, Paginator
from django.db.models import Q

from il2ks.core.achievements import ACHIEVEMENTS
from il2ks.db.models import AchievementHolders, PlayerAchievement, PlayerSortie, Tour
from il2ks.queries.paging import ROW_PAGE_SIZE

PAGE_SIZE = ROW_PAGE_SIZE
FEED_CANDIDATES = 40
"""How many of the newest rows the home feed looks at before it drops the common ones (it shows fewer)."""
FEED_MIN_RIBBON_TIER = 2
"""Ribbons enter the home feed from this tier up: a bronze ribbon is what everybody gets (doc 17, `[PROPOSED]`)."""


class Holding(NamedTuple):
    """How many visible pilots hold a tier in a scope, out of how many pilots there are in it (0 = unknown)."""

    holders: int
    pilots: int


type HoldingKey = tuple[int | None, str, int]  # scope (tour id, None = all time), achievement key, tier


def player_rows(player_id: int, tour: Tour | None) -> list[PlayerAchievement]:
    """Every tier the player holds in the scope, oldest key first (one query)."""
    return list(
        PlayerAchievement.objects.filter(player_id=player_id, tour=tour)
        .select_related("mission")
        .order_by("key", "tier")
    )


def sortie_rows(sortie: PlayerSortie) -> list[PlayerAchievement]:
    """The tiers first reached in this sortie, all time and in its tour (one query)."""
    return list(PlayerAchievement.objects.filter(sortie_id=sortie.pk).select_related("mission").order_by("key", "tier"))


def holder_counts(tour: Tour | None) -> dict[tuple[str, int], Holding]:
    """(key, tier) -> the holders and the pilots of the scope (one query); a tier nobody holds is missing."""
    return {(r.key, r.tier): Holding(r.holders, r.pilots) for r in AchievementHolders.objects.filter(tour=tour)}


def holdings(scopes: Iterable[int | None]) -> dict[HoldingKey, Holding]:
    """`holder_counts` for several scopes at once (the sortie page: all time and its tour); one query."""
    wanted = set(scopes)
    query = Q(tour_id__in=[t for t in wanted if t is not None])
    if None in wanted:
        query |= Q(tour__isnull=True)
    return {(r.tour_id, r.key, r.tier): Holding(r.holders, r.pilots) for r in AchievementHolders.objects.filter(query)}


def holders_page(key: str, tier: int, number: str | int, tour: Tour | None) -> Page:
    """One page of the visible pilots who hold `tier` of `key` in the scope, newest first (rows with `player`)."""
    rows = (
        PlayerAchievement.objects.filter(key=key, tier=tier, tour=tour, player__is_hidden=False)
        .select_related("player", "mission")
        .order_by("-earned_at", "pk")
    )
    return Paginator(rows, PAGE_SIZE).get_page(number)


def recent_rows(tour: Tour | None) -> list[PlayerAchievement]:
    """The newest tiers earned in the scope by visible players (the home feed's candidates, one query): no hall-of-shame
    entry, and ribbons only from `FEED_MIN_RIBBON_TIER`. The caller drops the common ones (it knows the rarity)."""
    medal_keys = [a.key for a in ACHIEVEMENTS if not a.shame and a.kind == "medal"]
    ribbon_keys = [a.key for a in ACHIEVEMENTS if not a.shame and a.kind == "ribbon"]
    wanted = Q(key__in=medal_keys) | Q(key__in=ribbon_keys, tier__gte=FEED_MIN_RIBBON_TIER)
    return list(
        PlayerAchievement.objects.filter(wanted, tour=tour, player__is_hidden=False)
        .select_related("player", "mission")
        .order_by("-earned_at", "-pk")[:FEED_CANDIDATES]
    )
