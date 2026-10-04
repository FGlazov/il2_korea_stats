"""Every public page, as URL builders over a seeded world: shared by the timing tests and documented for the load test.
A new public URL name must get a row here (a test fails otherwise)."""

from collections.abc import Callable
from dataclasses import dataclass

from tests.perf.seed import SeededWorld


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
    # site context 2, missions, last mission and its pilots, streaks, activity days, five compact boards, online now
    PageSpec("home", lambda w: "/", 12),
    PageSpec("leaderboards", lambda w: "/leaderboards/", 7),
    PageSpec("leaderboard", lambda w: "/leaderboards/interception/?tour=all", 7),
    PageSpec("leaderboard", lambda w: "/leaderboards/tank-busting/?pool=prop", 7),
    PageSpec("mission-list", lambda w: "/missions/", 6),
    PageSpec("mission-list", lambda w: "/missions/?page=3", 6),
    PageSpec("mission-detail", lambda w: f"/missions/{w.mission_pk}/", 6, max_ms=600.0),
    PageSpec("player-search", lambda w: "/players/", 5),
    PageSpec("player-search", lambda w: "/players/?q=Pilot+0", 5),
    PageSpec("player-detail", lambda w: f"/players/{w.player_pk}/", 14),
    PageSpec("player-sorties", lambda w: f"/players/{w.player_pk}/sorties/", 8),
    PageSpec("player-killboard", lambda w: f"/players/{w.player_pk}/killboard/", 7),
    PageSpec("streak-list", lambda w: "/streaks/", 5),
    PageSpec("player-streaks", lambda w: f"/players/{w.player_pk}/streaks/", 7),
    PageSpec("sortie-detail", lambda w: f"/sorties/{w.sortie_pk}/", 6),
    PageSpec("aircraft-list", lambda w: "/aircraft/", 5),
    PageSpec("aircraft-detail", lambda w: f"/aircraft/{w.aircraft_pk}/", 9),
    PageSpec("live", lambda w: "/live/", 4, max_ms=250.0),
)

NOT_PUBLIC_PAGES = frozenset({"set-language", "setup", "styleguide"})
"""URL names that are not pages a visitor reads: a form target, the first-run wizard, the DEBUG-only style guide."""
