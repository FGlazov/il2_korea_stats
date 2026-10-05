"""Reads for the killboard and streak sections that other pages embed: `{% load il2ks_boards %}`.

Simple tags that fetch and hand the rows to the including template (TD-22: plain SELECTs, `il2ks.queries.boards`). They
keep the profile and home views untouched: a section is one include line.

Tags: killboard_top (-> Board), type_killboard (-> queries.boards.TypeBoard),
player_streaks (-> list of PlayerStreak, one per track), player_tour_streaks (-> list of
PlayerBestStreak, one per track), home_streaks (-> list of HomeStreak). The first, the third and home_streaks take the
selected tour (None = all time; home_streaks then lists the longest streaks inside the tour, OQ-79). Also
player_builds (-> dict of AircraftBuild, with the filter build_of: the profile's favourite loadout per aircraft type).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from django import template

from il2ks.db.models import Player, PlayerBestStreak, PlayerKillboard, PlayerStreak, PlayerTourKillboard, Tour
from il2ks.queries import boards as reads
from il2ks.queries import builds as builds_reads
from il2ks.queries.builds import AircraftBuild

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
def player_streaks(player: Player) -> list[PlayerStreak]:
    """{% player_streaks player as streaks %}: the player's current and best ironman run on each track (air first),
    a track without a survived sortie missing."""
    return reads.streaks_of(player)


@register.simple_tag
def player_tour_streaks(player: Player, tour: Tour) -> list[PlayerBestStreak]:
    """{% player_tour_streaks player tour as bests %}: the player's longest run inside that tour on each track (air
    first), a track without a survived sortie there missing."""
    return reads.tour_streaks_of(player, tour)


@dataclass(frozen=True, slots=True)
class HomeStreak:
    """A row of the home page's streak list: the player and the streak's totals."""

    player: Player
    sorties: int
    kills_air: int
    flight_time_s: float


@register.simple_tag
def home_streaks(tour: Tour | None = None) -> list[HomeStreak]:
    """{% home_streaks tour as streaks %}: the longest running streaks of visible players (the home page block), or,
    with a tour (OQ-79), the longest streaks inside that tour."""
    if tour is not None:
        return [HomeStreak(r.player, r.sorties, r.kills_air, r.flight_time_s) for r in reads.longest_tour_streaks(tour)]
    return [
        HomeStreak(r.player, r.current_sorties, r.current_kills_air, r.current_flight_time_s)
        for r in reads.longest_current_streaks(datetime.now(UTC))
    ]


@register.simple_tag
def player_builds(player: Player, tour: Tour | None = None) -> dict[int, AircraftBuild]:
    """{% player_builds player tour as builds %}: what the player flies each aircraft type with (favourite loadout,
    weapon mods, gun ammo mix), all time or within `tour`. ONE query; look a type up with `builds|build_of:id`."""
    return builds_reads.player_builds(player, tour)


@register.filter
def build_of(builds: dict[int, AircraftBuild], aircraft_id: int) -> AircraftBuild | None:
    return builds.get(aircraft_id)
