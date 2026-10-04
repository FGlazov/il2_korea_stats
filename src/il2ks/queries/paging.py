"""Page sizes of every long list (OQ-96, NFR-PERF): one place, so the lists stay consistent.

Maintainer answer: "100% paginate. We only need to see like 10 missions and 20 sorties at a time." Mission rows are
tall (many columns, badges), so the mission list is short; every other list shows 20 rows."""

MISSION_PAGE_SIZE = 10
"""Rows of the mission list."""
ROW_PAGE_SIZE = 20
"""Rows of every other list: a player's sorties, one side's sorties on the mission page, players, leaderboards,
killboard, streaks, achievement holders, the kills of a mission."""
