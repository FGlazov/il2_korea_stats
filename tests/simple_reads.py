"""Harness for TD-22: views only run simple SELECT ... WHERE queries with simple joins."""

import re

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

# Aggregation, window functions and grouping are ingest-time work. COUNT(*) stays allowed for the paginator.
# The player profile's query budgets, shared by every test that checks them (keep them here, not per file).
# All time (`?tour=all`): context processor 2, player, names, tours, stat thresholds, aircraft rows, recent sorties,
# streak, killboard by aircraft type, killboard top victims and nemeses, tour history (charts).
PROFILE_READS_ALL_TIME = 13
PROFILE_READS_TOUR = PROFILE_READS_ALL_TIME + 1  # a tour, incl. the default current tour: + the PlayerTour row

FORBIDDEN_SQL = re.compile(r"\bGROUP\s+BY\b|\bHAVING\b|\b(SUM|AVG|MIN|MAX)\s*\(|\bOVER\s*\(", re.IGNORECASE)


def assert_simple_reads(client: Client, url: str, max_queries: int) -> None:
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200, f"{url} returned {response.status_code}"
    queries = [q["sql"] for q in ctx.captured_queries]
    assert len(queries) <= max_queries, f"{url} ran {len(queries)} queries (budget {max_queries}):\n" + "\n".join(
        queries
    )
    for sql in queries:
        assert not FORBIDDEN_SQL.search(sql), f"{url} aggregates at request time (TD-22): {sql}"
        assert sql.upper().count("SELECT") <= 1, f"{url} uses a subquery (TD-22): {sql}"


# Query budgets of the home page, shared by test_views, test_mission_pages and test_charts so they cannot drift apart.
HOME_EXTRA_READS = 6  # the five compact boards (Elo, interception, ground per hour, tank busting), the online snapshot
HOME_READS_EMPTY = 5 + HOME_EXTRA_READS
"""No missions: the site context (2), latest missions, streaks block, activity days, plus `HOME_EXTRA_READS`."""
HOME_READS = 6 + HOME_EXTRA_READS
"""With a last mission: the empty page's reads and the last mission's top pilots."""
