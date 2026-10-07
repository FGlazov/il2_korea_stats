"""Server-side response-time and query budgets for every public page (NFR-PERF-2, TD-22, TD-28).

Robust on slow runners by design: a warm-up request, then the **median** of several, against a generous budget (the
real target is ~300 ms on a normal machine; the budgets only catch order-of-magnitude regressions). Two cases:

- cache disabled: a plain GET, so the whole view and template run;
- cache enabled: a revalidation with `If-None-Match` (TD-28), answered 304 before the view runs.

The world is `tests.perf.seed`: a few thousand sorties, so lists paginate, and the query budgets double as N+1 guards
(a page that runs a query per row blows its budget here but might pass on the tiny worlds of the other tests).
Set `IL2KS_PERF_REPORT=1` (with `-s`) to print the measured medians."""

import os
import statistics
import time
from collections.abc import Callable

import pytest
from django.db import connection
from django.test import Client
from django.urls import URLPattern, get_resolver
from tests.perf.pages import NOT_PUBLIC_PAGES, PAGES, PageSpec
from tests.perf.seed import SeededWorld
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

REQUESTS = 7
REVALIDATION_MAX_MS = 100.0
"""A 304 runs one query and no view code: even a slow runner stays far below this."""
REPORT = os.environ.get("IL2KS_PERF_REPORT") == "1"


def _id(spec: PageSpec) -> str:
    return spec.url(_PLACEHOLDER)


_PLACEHOLDER = SeededWorld(0, 0, 0, 0, "", 0, 0, [], [], [], [], [])


def median_ms(client: Client, url: str, headers: dict[str, str] | None = None, expect: int = 200) -> float:
    """Median wall time of `REQUESTS` requests after one warm-up (template cache, connection, ORM caches)."""
    client.get(url, headers=headers)
    times: list[float] = []
    for _ in range(REQUESTS):
        start = time.perf_counter()
        response = client.get(url, headers=headers)
        times.append((time.perf_counter() - start) * 1000)
        assert response.status_code == expect, f"{url} answered {response.status_code}"
    median = statistics.median(times)
    if REPORT:
        print(f"PERF {expect} {url} median {median:.1f} ms")
    return median


def test_the_world_is_big_enough(big_world: SeededWorld) -> None:
    """Guards the guard: budgets on an almost empty database would prove nothing."""
    assert big_world.mission_count >= 50
    assert big_world.sortie_count >= 1500


@pytest.mark.parametrize("spec", PAGES, ids=_id)
def test_query_budget_at_scale(client: Client, big_world: SeededWorld, spec: PageSpec) -> None:
    assert_simple_reads(client, spec.url(big_world), max_queries=spec.max_queries)


@pytest.mark.parametrize("spec", PAGES, ids=_id)
def test_full_render_time(client: Client, big_world: SeededWorld, spec: PageSpec) -> None:
    """Cache disabled: no `If-None-Match`, so every request runs the view and renders the template."""
    elapsed = median_ms(client, spec.url(big_world))

    assert elapsed < spec.max_ms, f"{spec.url(big_world)}: median {elapsed:.0f} ms (budget {spec.max_ms:.0f} ms)"


@pytest.mark.parametrize("spec", [s for s in PAGES if s.url_name not in {"live", "healthz"}]  # never cached: no 304, ids=_id)
def test_revalidation_is_cheap(client: Client, big_world: SeededWorld, spec: PageSpec) -> None:
    """Cache enabled: a returning visitor's conditional GET is a 304 with exactly one query (TD-28)."""
    url = spec.url(big_world)
    headers = {"If-None-Match": client.get(url)["ETag"]}

    queries: list[str] = []

    def count(execute: Callable[..., object], sql: str, params: object, many: bool, context: object) -> object:
        queries.append(sql)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(count):
        client.get(url, headers=headers)  # (a wrapper, not CaptureQueriesContext: every request resets the query log)
    elapsed = median_ms(client, url, headers=headers, expect=304)

    assert len(queries) == 1, f"{url}: a 304 should cost one query, ran {len(queries)}"
    assert elapsed < REVALIDATION_MAX_MS, f"{url}: 304 median {elapsed:.0f} ms (budget {REVALIDATION_MAX_MS:.0f} ms)"


def test_every_public_page_has_a_budget() -> None:
    """A new public URL name needs a row in `tests/perf/pages.py`: a query budget and a time budget."""
    patterns = [p for p in get_resolver("il2ks.web.urls").url_patterns if isinstance(p, URLPattern)]
    names = {p.name for p in patterns if p.name} - NOT_PUBLIC_PAGES
    covered = {spec.url_name for spec in PAGES}

    assert names == covered, (
        f"without a performance budget: {sorted(names - covered)}; stale: {sorted(covered - names)}"
    )
