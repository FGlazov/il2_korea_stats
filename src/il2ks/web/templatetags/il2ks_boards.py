"""Reads for the killboard and streak sections that other pages embed: `{% load il2ks_boards %}`.

Simple tags that fetch and hand the rows to the including template (TD-22: plain SELECTs, `il2ks.queries.boards`). They
keep the profile and home views untouched: a section is one include line.

Tags: killboard_top (-> Board), player_streak (-> PlayerStreak or None), home_streaks (-> list of PlayerStreak).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from django import template

from il2ks.db.models import Player, PlayerKillboard, PlayerStreak
from il2ks.queries import boards as reads

register = template.Library()


@dataclass(frozen=True, slots=True)
class Board:
    """The profile's killboard section: top victims and top nemeses."""

    victims: list[PlayerKillboard]
    nemeses: list[PlayerKillboard]


@register.simple_tag
def killboard_top(player: Player) -> Board:
    """{% killboard_top player as board %}: the opponents the player shot down most, and who shot them down most."""
    return Board(reads.top_victims(player), reads.top_nemeses(player))


@register.simple_tag
def player_streak(player: Player) -> PlayerStreak | None:
    """{% player_streak player as streak %}: the player's ironman streaks, None without a survived sortie."""
    return reads.streak_of(player)


@register.simple_tag
def home_streaks() -> list[PlayerStreak]:
    """{% home_streaks as streaks %}: the longest running streaks of visible players (the home page block)."""
    return reads.longest_current_streaks(datetime.now(UTC))
