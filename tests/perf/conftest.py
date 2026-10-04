"""Performance tests (`pytest -m perf`, doc 08): timing and query budgets over a bigger seeded world.

Everything under tests/perf is marked `perf` (see `tests/conftest.py`). They run in the normal suite (the budgets are
generous); `-m "not perf"` skips them, `-m perf` runs only them."""

from collections.abc import Iterator

import pytest
from django.db import connection, transaction
from pytest_django.plugin import DjangoDbBlocker
from tests.perf.seed import SeededWorld, fill


def analyze_seeded_tables() -> None:
    """Postgres plans from table statistics, and the seeded rows are uncommitted, so autovacuum cannot see them: after
    the ~700 earlier tests (which analysed the empty tables) the planner believes every table has one row and chooses
    nested loops with full index scans and join filters: `/missions/<id>/` took 59 s in CI (12 s with 25 rolled-back
    seedings locally) while SQLite and a fresh Postgres database took 30 ms. `ANALYZE` sees this transaction's rows."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE")


@pytest.fixture(scope="module")
def big_world(django_db_setup: None, django_db_blocker: DjangoDbBlocker) -> Iterator[SeededWorld]:
    """Seed once per module inside an outer transaction that is rolled back at the end, so other tests never see it
    (the tests' own `django_db` transactions nest as savepoints)."""
    with django_db_blocker.unblock():
        atomic = transaction.atomic()
        atomic.__enter__()
        try:
            world = fill()
            analyze_seeded_tables()
            yield world
        finally:
            transaction.set_rollback(True)
            atomic.__exit__(None, None, None)
