"""Performance tests (`pytest -m perf`, doc 08): timing and query budgets over a bigger seeded world.

Everything under tests/perf is marked `perf` (see `tests/conftest.py`). They run in the normal suite (the budgets are
generous); `-m "not perf"` skips them, `-m perf` runs only them."""

from collections.abc import Iterator

import pytest
from django.db import transaction
from pytest_django.plugin import DjangoDbBlocker

from tests.perf.seed import SeededWorld, fill


@pytest.fixture(scope="module")
def big_world(django_db_setup: None, django_db_blocker: DjangoDbBlocker) -> Iterator[SeededWorld]:
    """Seed once per module inside an outer transaction that is rolled back at the end, so other tests never see it
    (the tests' own `django_db` transactions nest as savepoints)."""
    with django_db_blocker.unblock():
        atomic = transaction.atomic()
        atomic.__enter__()
        try:
            yield fill()
        finally:
            transaction.set_rollback(True)
            atomic.__exit__(None, None, None)
