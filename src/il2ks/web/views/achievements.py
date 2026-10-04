"""Medals: a player's full list, the overview of all achievements and the holders of one tier (FR-WEB-26, doc 17).

Simple reads from `il2ks.queries.achievements` (TD-22). A hidden player has no list (404) and is never listed as a
holder (FR-ADM-3); the holder counts on the overview count visible players only.
"""

from dataclasses import dataclass

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from il2ks.core.achievements import BY_KEY
from il2ks.queries import achievements as reads
from il2ks.queries import players as player_reads
from il2ks.web import medals


@dataclass(frozen=True, slots=True)
class OverviewTier:
    tier: medals.Tier
    holders: int


@dataclass(frozen=True, slots=True)
class OverviewRow:
    info: medals.MedalInfo
    tiers: tuple[OverviewTier, ...]


@dataclass(frozen=True, slots=True)
class ListTier:
    tier: medals.Tier
    earned: medals.Medal | None


@dataclass(frozen=True, slots=True)
class ListRow:
    info: medals.MedalInfo
    tiers: tuple[ListTier, ...]
    best: int  # highest tier held, 0 = none


def achievement_overview(request: HttpRequest) -> HttpResponse:
    """`/achievements/`: every achievement with its tiers and how many pilots hold each.

    Template `il2ks/achievements/overview.html`. Context: `rows` (OverviewRow), `page_title`."""
    counts = reads.holder_counts()
    rows = [
        OverviewRow(info, tuple(OverviewTier(t, counts.get((info.key, t.number), 0)) for t in info.tiers))
        for info in medals.all_info()
    ]
    return render(
        request,
        "il2ks/achievements/overview.html",
        {"rows": rows, "page_title": _("Achievements"), "crumbs": [(_("Achievements"), None)]},
    )


def achievement_holders(request: HttpRequest, key: str) -> HttpResponse:
    """`/achievements/<key>/?tier=&page=`: the visible pilots who hold a tier, newest first. Without a valid `tier` it
    is the highest tier anybody holds (tier 1 when nobody does).

    Template `il2ks/achievements/holders.html`. Context: `info`, `tier` (the shown Tier), `tiers` (OverviewTier,
    to switch), `page_obj` (PlayerAchievement rows with `player` and `mission`), `crumbs`, `page_title`."""
    achievement = BY_KEY.get(key)
    if achievement is None:
        raise Http404
    info = medals.info(achievement)
    counts = reads.holder_counts()
    tiers = tuple(OverviewTier(t, counts.get((key, t.number), 0)) for t in info.tiers)
    try:
        wanted = int(request.GET.get("tier", ""))
    except ValueError:
        wanted = 0
    if not 1 <= wanted <= achievement.top_tier:
        wanted = max((t.tier.number for t in tiers if t.holders), default=1)
    context = {
        "info": info,
        "tier": info.tiers[wanted - 1],
        "tiers": tiers,
        "page_obj": reads.holders_page(key, wanted, request.GET.get("page", 1)),
        "page_title": _("%(medal)s: holders") % {"medal": info.name},
        "crumbs": [(_("Achievements"), reverse("web:achievements")), (str(info.name), None)],
    }
    return render(request, "il2ks/achievements/holders.html", context)


def player_achievements(request: HttpRequest, pk: int) -> HttpResponse:
    """`/players/<pk>/achievements/`: every achievement with the player's tiers (earned ones dated) and the next ones
    still to earn. 404 for a missing or hidden player.

    Template `il2ks/players/achievements.html`. Context: `player`, `rows` (ListRow), `earned` (tiers held), `crumbs`,
    `page_title`."""
    player = player_reads.visible_player(pk)
    if player is None:
        raise Http404
    held = {(m.key, m.tier): m for m in medals.medals_of(reads.player_rows(player.pk), highest_only=False)}
    rows: list[ListRow] = []
    for info in medals.all_info():
        tiers = tuple(ListTier(t, held.get((info.key, t.number))) for t in info.tiers)
        rows.append(ListRow(info, tiers, max((t.tier.number for t in tiers if t.earned), default=0)))
    context = {
        "player": player,
        "rows": rows,
        "earned": len(held),
        "page_title": _("%(name)s: achievements") % {"name": player.current_name},
        "crumbs": [
            (_("Players"), reverse("web:player-search")),
            (player.current_name, reverse("web:player-detail", args=[player.pk])),
            (_("Achievements"), None),
        ],
    }
    return render(request, "il2ks/players/achievements.html", context)
