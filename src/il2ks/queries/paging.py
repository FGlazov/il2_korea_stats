"""Page sizes of every long list (OQ-96, NFR-PERF): one place, so the lists stay consistent.

Maintainer answer: "100% paginate. We only need to see like 10 missions and 20 sorties at a time." Mission rows are
tall (many columns, badges), so the mission list is short; every other list shows 20 rows."""

MISSION_PAGE_SIZE = 10
"""Rows of the mission list."""
ROW_PAGE_SIZE = 20
"""Rows of every other list: a player's sorties, one side's sorties on the mission page, players, leaderboards,
killboard, streaks, achievement holders, the kills of a mission."""

MIN_EVENTS_LISTED = 10
"""A row of the aircraft page's breakdown tables needs at least this many events to be listed (maintainer 2026-10-05):
kills for an ammunition mix (a mix is a set of kills), sorties for a loadout and a modification set (a sortie is what
a loadout is flown in). Read at call time (`paging.MIN_EVENTS_LISTED`), so a test can lower it."""
