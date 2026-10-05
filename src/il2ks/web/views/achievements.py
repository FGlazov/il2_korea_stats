"""Medals: a player's full list, the overview of all achievements and the holders of one tier (FR-WEB-26, doc 17).

Simple reads from `il2ks.queries.achievements` (TD-22). A hidden player has no list (404) and is never listed as a
holder (FR-ADM-3); the holder counts on the overview count visible players only. All three follow the tour rule (TD-26):
no `?tour` is the current tour, `?tour=all` all time, `?tour=<id>` that tour; the template shows `{% tour_select %}`.
"""

from dataclasses import dataclass

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from il2ks.queries import achievements as reads
from il2ks.queries import players as player_reads
from il2ks.queries.tours import pilot_absence, tour_choice_from, tour_query
from il2ks.web import medals
from il2ks.web.achievement_config import AchievementConfig
from il2ks.web.context_processors import site_row


@dataclass(frozen=True, slots=True)
class OverviewTier:
    tier: medals.Tier
    holders: int
    rarity: medals.Rarity


@dataclass(frozen=True, slots=True)
class OverviewRow:
    info: medals.MedalInfo
    tiers: tuple[OverviewTier, ...]


@dataclass(frozen=True, slots=True)
class ListTier:
    tier: medals.Tier
    earned: medals.Medal | None
    rarity: medals.Rarity


@dataclass(frozen=True, slots=True)
class ListRow:
    info: medals.MedalInfo
    tiers: tuple[ListTier, ...]
    best: int  # highest tier held, 0 = none


def achievement_overview(request: HttpRequest) -> HttpResponse:
    """`/achievements/`: every achievement with its tiers and how many pilots hold each.

    Template `il2ks/achievements/overview.html`. Context: `rows` (OverviewRow), `tours`, `tour`, `tour_query`,
    `page_title`. Medals first, then ribbons, then the hall-of-shame ribbons (each group in registry order)."""
    choice = tour_choice_from(request.GET)
    config = AchievementConfig.from_row(site_row(request))
    counts = reads.holder_counts(choice.selected)
    rows = [
        OverviewRow(
            info,
            tuple(_overview_tier(info, t, counts) for t in info.tiers),
        )
        for info in medals.all_info(config, all_time=choice.selected is None)
    ]
    rows.sort(key=lambda r: (r.info.shame, r.info.kind == "ribbon"))
    context = {
        "rows": rows,
        "tour_query": tour_query(choice.selected),
        "page_title": _("Achievements"),
        "crumbs": [(_("Achievements"), None)],
        **choice.context,
    }
    return render(request, "il2ks/achievements/overview.html", context)


def _overview_tier(
    info: medals.MedalInfo, tier: medals.Tier, counts: dict[tuple[str, int], reads.Holding]
) -> OverviewTier:
    holding = counts.get((info.key, tier.number))
    return OverviewTier(tier, holding.holders if holding else 0, medals.rarity(holding, shame=info.shame))


def achievement_holders(request: HttpRequest, key: str) -> HttpResponse:
    """`/achievements/<key>/?tier=&page=`: the visible pilots who hold a tier, newest first. Without a valid `tier` it
    is the highest tier anybody holds (tier 1 when nobody does).

    Template `il2ks/achievements/holders.html`. Context: `info`, `tier` (the shown Tier), `tiers` (OverviewTier,
    to switch), `rarity` (of the shown tier), `page_obj` (PlayerAchievement rows with `player` and `mission`), `tours`,
    `tour`, `tour_query`, `crumbs`, `page_title`."""
    config = AchievementConfig.from_row(site_row(request))
    achievement = config.achievement(key)  # None: unknown, or switched off by the admin
    choice = tour_choice_from(request.GET)
    if achievement is None or (achievement.all_time_only and choice.selected is not None):  # no tour view of it
        raise Http404
    info = medals.info(achievement, config, all_time=choice.selected is None)
    counts = reads.holder_counts(choice.selected)
    tiers = tuple(_overview_tier(info, t, counts) for t in info.tiers)
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
        "rarity": tiers[wanted - 1].rarity,
        "page_obj": reads.holders_page(key, wanted, request.GET.get("page", 1), choice.selected),
        "tour_query": tour_query(choice.selected),
        **choice.context,
        "page_title": _("%(medal)s: holders") % {"medal": info.name},
        "crumbs": [(_("Achievements"), reverse("web:achievements")), (str(info.name), None)],
    }
    return render(request, "il2ks/achievements/holders.html", context)


def player_achievements(request: HttpRequest, pk: int) -> HttpResponse:
    """`/players/<pk>/achievements/`: every achievement with the player's tiers (earned ones dated) and the next ones
    still to earn. 404 for a missing or hidden player.

    Template `il2ks/players/achievements.html`. Context: `player`, `rows` (ListRow), `earned` (tiers held), `tours`,
    `tour`, `tour_query`, `crumbs`, `page_title`."""
    player = player_reads.visible_player(pk)
    if player is None:
        raise Http404
    choice = tour_choice_from(request.GET)
    config = AchievementConfig.from_row(site_row(request))
    scope = choice.selected.pk if choice.selected else None
    holdings = reads.holdings([scope])
    held = {
        (m.key, m.tier): m
        for m in medals.medals_of(reads.player_rows(player.pk, choice.selected), holdings, config, highest_only=False)
    }
    rows: list[ListRow] = []
    for info in medals.all_info(config, all_time=choice.selected is None):
        tiers = tuple(
            ListTier(
                t,
                held.get((info.key, t.number)),
                medals.rarity(holdings.get((scope, info.key, t.number)), shame=info.shame),
            )
            for t in info.tiers
        )
        rows.append(ListRow(info, tiers, max((t.tier.number for t in tiers if t.earned), default=0)))
    rows.sort(key=lambda r: (r.info.shame, r.info.kind == "ribbon"))
    context = {
        "player": player,
        "rows": rows,
        "earned": len(held),
        "absence": None if held else pilot_absence(player.pk, choice),
        "tour_query": tour_query(choice.selected),
        **choice.context,
        "page_title": _("%(name)s: achievements") % {"name": player.current_name},
        "crumbs": [
            (_("Players"), reverse("web:player-search")),
            (player.current_name, reverse("web:player-detail", args=[player.pk]) + tour_query(choice.selected)),
            (_("Achievements"), None),
        ],
    }
    return render(request, "il2ks/players/achievements.html", context)
