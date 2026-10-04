"""Reads for the killboard and streak sections that other pages embed: `{% load il2ks_boards %}`.

Simple tags that fetch and hand the rows to the including template (TD-22: plain SELECTs, `il2ks.queries.boards`). They
keep the profile and home views untouched: a section is one include line.

Tags: killboard_top (-> Board), type_killboard (-> queries.boards.TypeBoard),
player_streak (-> PlayerStreak or None), player_tour_streak (-> PlayerBestStreak or
None), home_streaks (-> list of PlayerStreak). The first and the third take the selected tour (None = all time).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from django import template

from il2ks.db.models import Player, PlayerBestStreak, PlayerKillboard, PlayerStreak, PlayerTourKillboard, Tour
from il2ks.queries import boards as reads

register = template.Library()


@dataclass(frozen=True, slots=True)
class Board:
    """The profile's killboard section: top victims and top nemeses."""

    victims: Sequence[PlayerKillboard | PlayerTourKillboard]
    nemeses: Sequence[PlayerKillboard | PlayerTourKillboard]


@register.simple_tag
def killboard_top(player: Player, tour: Tour | None = None) -> Board:
    """{% killboard_top player tour as board %}: the opponents the player shot down most, and who shot them down most
    (in `tour` when given)."""
    return Board(reads.top_victims(player, tour), reads.top_nemeses(player, tour))


@register.simple_tag
def type_killboard(player: Player, tour: Tour | None = None, limit: int = reads.TOP_TYPES) -> reads.TypeBoard:
    """{% type_killboard player tour as types %}: the enemy aircraft types the player shot down most and the types
    that shot them down most (in `tour` when given), `limit` each. One query."""
    return reads.type_board(player, tour, limit)


@register.simple_tag
def player_streak(player: Player) -> PlayerStreak | None:
    """{% player_streak player as streak %}: the player's ironman streaks, None without a survived sortie."""
    return reads.streak_of(player)


@register.simple_tag
def player_tour_streak(player: Player, tour: Tour) -> PlayerBestStreak | None:
    """{% player_tour_streak player tour as best %}: the player's longest streak inside that tour, None without one."""
    return reads.best_streak_of(player, tour)


@register.simple_tag
def home_streaks() -> list[PlayerStreak]:
    """{% home_streaks as streaks %}: the longest running streaks of visible players (the home page block)."""
    return reads.longest_current_streaks(datetime.now(UTC))
