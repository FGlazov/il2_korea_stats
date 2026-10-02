"""Harness for TD-22: views only run simple SELECT ... WHERE queries with simple joins."""

import re

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

# Aggregation, window functions and grouping are ingest-time work. COUNT(*) stays allowed for the paginator.
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
