"""Reads for the medal blocks that other pages embed: `{% load il2ks_achievements %}`.

Simple tags that fetch and hand the rows to the including template (TD-22: plain SELECTs, `il2ks.queries.achievements`):
`player_medals player tour as medal_set` (the best tier of each achievement the player holds in the tour, or all time
for tour None, split into medals, ribbons and hall-of-shame ribbons), `sortie_medals sortie as medals` (the tiers first
reached in that sortie, all time and in its tour) and `recent_medals tour as feed` (the home page's "Recently earned").
Two queries each (the rows, and the holder counts for the rarity), one when there are no rows.
"""

from dataclasses import dataclass

from django import template
from django.template import Context

from il2ks.db.models import Player, PlayerSortie, SiteSettings, Tour
from il2ks.queries import achievements as reads
from il2ks.web import medals
from il2ks.web.achievement_config import DEFAULT_CONFIG, AchievementConfig

register = template.Library()

FEED_SHOWN = 8
"""How many entries the home feed shows."""


@dataclass(frozen=True, slots=True)
class FeedItem:
    """One entry of the home feed: the tier reached and who reached it."""

    medal: medals.Medal
    player_id: int
    player_name: str


def achievement_config(context: Context) -> AchievementConfig:
    """The admin's achievement choices, from the `site` row the context processor already read (no query); the
    built-in set, when a template is rendered without it."""
    site = context.get("site")
    return AchievementConfig.from_row(site) if isinstance(site, SiteSettings) else DEFAULT_CONFIG


@register.simple_tag(takes_context=True)
def player_medals(context: Context, player: Player, tour: Tour | None = None) -> medals.MedalSet:
    rows = reads.player_rows(player.pk, tour)
    holdings = reads.holdings([tour.pk if tour else None]) if rows else {}
    return medals.medal_set(rows, holdings, achievement_config(context))


@register.simple_tag(takes_context=True)
def sortie_medals(context: Context, sortie: PlayerSortie) -> list[medals.Medal]:
    rows = reads.sortie_rows(sortie)
    holdings = reads.holdings({r.tour_id for r in rows}) if rows else {}
    found = [
        m for m in medals.medals_of(rows, holdings, achievement_config(context), highest_only=False) if not m.shame
    ]  # shame: hall of shame only
    all_time = {(m.key, m.tier) for m in found if m.scope is None}
    # The same tier in the tour is the same news (the first tour starts with the server): list it once.
    found = [m for m in found if m.scope is None or (m.key, m.tier) not in all_time]
    return sorted(found, key=lambda m: m.scope is not None)  # all time first; `regroup` needs the scopes together


@register.simple_tag(takes_context=True)
def recent_medals(context: Context, tour: Tour | None = None) -> list[FeedItem]:
    """The home feed: the newest tiers earned in the scope by visible players, newest first, `FEED_SHOWN` at most.
    Hall-of-shame entries and bronze ribbons are never in it (the query), and neither is any tier that
    `COMMON_FEED_FROM` percent of the pilots hold already (doc 17, `[PROPOSED]`)."""
    config = achievement_config(context)
    rows = reads.recent_rows(tour, config.active())
    if not rows:
        return []
    holdings = reads.holdings([tour.pk if tour else None])
    feed: list[FeedItem] = []
    for row in rows:
        found = medals.medals_of([row], holdings, config, highest_only=False)
        if not found:
            continue
        medal = found[0]
        share = medal.rarity.share
        if share is not None and share >= medals.COMMON_FEED_FROM:
            continue
        feed.append(FeedItem(medal, row.player_id, row.player.current_name))
        if len(feed) == FEED_SHOWN:
            break
    return feed
