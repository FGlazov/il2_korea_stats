"""Reads behind the medals (FR-WEB-26, doc 17). Simple SELECTs only (TD-22).

They read the level-2 tables `PlayerAchievement` and `AchievementHolders` (`ingest.achievements`). Hiding (FR-ADM-3):
callers pass a visible player; the holder lists and the counts leave hidden players out. A medal's sortie is linked
only while its mission is visible (the templates check `mission_hidden`).
"""

from django.core.paginator import Page, Paginator

from il2ks.db.models import AchievementHolders, PlayerAchievement, PlayerSortie
from il2ks.queries.paging import ROW_PAGE_SIZE

PAGE_SIZE = ROW_PAGE_SIZE


def player_rows(player_id: int) -> list[PlayerAchievement]:
    """Every tier the player holds, oldest key first (one query)."""
    return list(PlayerAchievement.objects.filter(player_id=player_id).select_related("mission").order_by("key", "tier"))


def sortie_rows(sortie: PlayerSortie) -> list[PlayerAchievement]:
    """The tiers first reached in this sortie (one query)."""
    return list(PlayerAchievement.objects.filter(sortie_id=sortie.pk).select_related("mission").order_by("key", "tier"))


def holder_counts() -> dict[tuple[str, int], int]:
    """(key, tier) -> visible pilots holding it (one query); a tier nobody holds is missing."""
    return {(r.key, r.tier): r.holders for r in AchievementHolders.objects.all()}


def holders_page(key: str, tier: int, number: str | int) -> Page:
    """One page of the visible pilots who hold `tier` of `key`, newest first (rows with `player`)."""
    rows = (
        PlayerAchievement.objects.filter(key=key, tier=tier, player__is_hidden=False)
        .select_related("player", "mission")
        .order_by("-earned_at", "pk")
    )
    return Paginator(rows, PAGE_SIZE).get_page(number)
