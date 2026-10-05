"""Every public page, as URL builders over a seeded world: shared by the timing tests and documented for the load test.
A new public URL name must get a row here (a test fails otherwise)."""

from collections.abc import Callable
from dataclasses import dataclass

from tests.perf.seed import SeededWorld
from tests.simple_reads import HOME_READS, PROFILE_READS_TOUR


@dataclass(frozen=True)
class PageSpec:
    """One public page: how to build its URL, how many queries it may run, how long the server may take."""

    url_name: str
    url: Callable[[SeededWorld], str]
    max_queries: int
    """Constant in the data size: a page that runs one query per row fails here on the bigger world (N+1)."""
    max_ms: float = 400.0
    """Budget for the median full render (no cache) on a slow CI runner. NFR-PERF-2 says ~300 ms on real hardware."""


PAGES: tuple[PageSpec, ...] = (
    PageSpec("home", lambda w: "/", HOME_READS),
    PageSpec("leaderboards", lambda w: "/leaderboards/", 8),
    PageSpec("leaderboard", lambda w: "/leaderboards/interception/?tour=all", 8),
    PageSpec("leaderboard", lambda w: "/leaderboards/tank-busting/?pool=prop", 8),
    PageSpec("mission-list", lambda w: "/missions/", 6),
    PageSpec("mission-list", lambda w: "/missions/?page=3", 6),
    PageSpec("mission-detail", lambda w: f"/missions/{w.mission_pk}/", 6, max_ms=600.0),
    PageSpec("player-search", lambda w: "/players/", 5),
    PageSpec("player-search", lambda w: "/players/?q=Pilot+0", 5),
    PageSpec("player-detail", lambda w: f"/players/{w.player_pk}/", PROFILE_READS_TOUR),  # incl. the medal row
    PageSpec("player-sorties", lambda w: f"/players/{w.player_pk}/sorties/", 8),
    PageSpec("sortie-list", lambda w: "/sorties/", 6),  # site context (2), tours, aircraft choices, count, page
    PageSpec("sortie-list", lambda w: "/sorties/?tour=all&page=3", 6),
    PageSpec("player-killboard", lambda w: f"/players/{w.player_pk}/killboard/", 7),
    PageSpec("player-streaks", lambda w: f"/players/{w.player_pk}/streaks/", 8),
    PageSpec("player-streak-runs", lambda w: f"/players/{w.player_pk}/streaks/history/", 6),
    PageSpec("leaderboard", lambda w: "/leaderboards/ironman-all/", 8),  # the best runs + the running ones
    PageSpec("leaderboard", lambda w: "/leaderboards/ironman-air/", 8),
    PageSpec("leaderboard", lambda w: "/leaderboards/ironman-ground/?tour=all", 8),
    PageSpec("leaderboard", lambda w: "/leaderboards/air/", 8),
    PageSpec("sortie-detail", lambda w: f"/sorties/{w.sortie_pk}/", 8),  # + earned medals and their rarity (FR-WEB-26)
    PageSpec("player-achievements", lambda w: f"/players/{w.player_pk}/achievements/", 6),
    PageSpec("achievements", lambda w: "/achievements/", 5),
    PageSpec("achievement-holders", lambda w: "/achievements/flight_hours/", 7),
    PageSpec("aircraft-list", lambda w: "/aircraft/", 5),
    PageSpec(
        "aircraft-detail", lambda w: f"/aircraft/{w.aircraft_pk}/", 12
    ),  # + the tour tiles, the ammo mixes, the mods table
    PageSpec("live", lambda w: "/live/", 4, max_ms=250.0),
)

NOT_PUBLIC_PAGES = frozenset({"set-language", "setup", "sprite", "styleguide", "streak-list"})
"""URL names that are not pages a visitor reads: a form target, the first-run wizard, the DEBUG-only style guide, the
old `/streaks/` URL (a permanent redirect to the ironman board, which has its own rows)."""
