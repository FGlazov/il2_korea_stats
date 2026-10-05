"""Every query a public page or the ingest runs can use an index (maintainer request 2026-10-04).

The pages are the ones of `tests.perf.pages.PAGES` plus their filter and sort variants (`plan_variants`), rendered over
the seeded world of `tests.perf.seed`; the ingest is one pass over the fixture missions into that same world. Each
captured statement gets `EXPLAIN QUERY PLAN`; the rules and the allow-lists are in `tests.perf.query_plans`, how to
read a failure is in docs/performance-testing.md ("Query plans"). SQLite only: Postgres plans from statistics, and
its seq scans on a seeded few thousand rows prove nothing."""

import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import pytest
from django.apps import apps
from django.db import connection
from django.test import Client
from tests.conftest import FIXTURE_LOGS
from tests.ingest_fakes import make_config
from tests.perf import query_plans as qp
from tests.perf.pages import PAGES
from tests.perf.plan_variants import VARIANTS, Variant
from tests.perf.seed import SeededWorld

from il2ks.ingest.runner import IngestOptions, default_pipeline, ingest_once

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(connection.vendor != "sqlite", reason="EXPLAIN QUERY PLAN is SQLite's"),
]

type Captured = list[qp.Statement]


def capture[T](work: Callable[[], T], *, require_where: bool = False) -> tuple[T, Captured]:
    """Run `work`, return its result and every statement it executed (deduplicated, in order)."""
    seen: dict[tuple[str, tuple[qp.SqlParam, ...]], qp.Statement] = {}

    def record(execute: Callable[..., object], sql: str, params: object, many: bool, context: object) -> object:
        if not many and qp.checkable(sql, require_where=require_where):
            key = (sql, tuple(cast("Sequence[qp.SqlParam]", params)) if isinstance(params, (list, tuple)) else ())
            seen.setdefault(key, qp.Statement(sql, key[1]))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(record):
        result = work()
    return result, list(seen.values())


def failures_of(where: str, statements: Captured, url: str, *, rare_sort: bool = False) -> list[str]:
    """One readable block per statement whose plan breaks the rules and has no allowance."""
    blocks: list[str] = []
    for statement in statements:
        plan = qp.explain(statement)
        found = [
            p
            for p in qp.problems(plan, statement.sql, rare_sort=rare_sort)
            if qp.allowance_reason(p, statement.sql, url) is None
        ]
        if found:
            blocks.append(qp.format_failure(where, statement.sql, plan, found))
    return blocks


def test_every_page_in_the_budget_list_has_a_plan_variant() -> None:
    """The variants cover every row of `PAGES` (same URL builders), so a new page is plan-checked automatically."""
    assert {spec.url_name for spec in PAGES} <= {v.url_name for v in VARIANTS}


@pytest.mark.parametrize("variant", VARIANTS, ids=lambda v: v.label)
def test_page_queries_use_an_index(client: Client, big_world: SeededWorld, variant: Variant) -> None:
    url = variant.url(big_world)
    response, statements = capture(lambda: client.get(url))
    assert response.status_code == 200, f"{url} answered {response.status_code}"
    assert statements, f"{url} ran no query: the capture is broken"

    blocks = failures_of(url, statements, url, rare_sort=variant.rare_sort)
    assert not blocks, f"{len(blocks)} statement(s) of {url} cannot use an index:\n\n" + "\n\n".join(blocks)


def test_small_table_names_are_real_tables() -> None:
    """A renamed model must not silently turn its allow-list entry into a no-op (or a stale excuse)."""
    tables = {m._meta.db_table for m in apps.get_app_config("il2ks_db").get_models()}
    assert set(qp.SMALL_TABLES) <= tables, f"not tables: {sorted(set(qp.SMALL_TABLES) - tables)}"
    for entry in (*qp.SCAN_ALLOWANCES, *qp.SORT_ALLOWANCES):
        assert entry.reason.strip(), f"allowance without a reason: {entry}"
        assert entry.table in ("", *tables), f"unknown table in allowance: {entry}"
        assert entry.table not in qp.SMALL_TABLES, f"{entry.table} is already allowed as a small table"


def test_the_rule_flags_a_scan_and_accepts_an_index() -> None:
    """The checker itself: bare scan fails, scan through an index and searches pass, small tables are exempt."""
    assert [p.kind for p in qp.problems(["SCAN il2ks_db_playersortie"])] == ["scan"]
    assert qp.problems(["SCAN il2ks_db_playersortie USING COVERING INDEX x"]) == []
    assert qp.problems(["SCAN il2ks_db_playersortie USING INDEX x"]) == []
    assert qp.problems(["SEARCH il2ks_db_playersortie USING INDEX x (player_id=?)"]) == []
    assert qp.problems(["SCAN il2ks_db_tour"]) == []
    sort = ["SEARCH il2ks_db_playersortie USING INDEX x (player_id=?)", "USE TEMP B-TREE FOR ORDER BY"]
    limited = "SELECT x FROM t ORDER BY y LIMIT 5"
    assert [p.kind for p in qp.problems(sort, limited)] == ["sort"]
    assert qp.problems(sort, "SELECT x FROM t ORDER BY y") == [], "no LIMIT: every row is returned anyway"
    assert qp.problems(sort, limited, rare_sort=True) == [], "a rare sort may sort its scope"
    assert [p.kind for p in qp.problems(["SCAN il2ks_db_playersortie"], "SELECT COUNT(*) FROM t", rare_sort=True)] == [
        "scan"
    ], "a count has no ORDER BY: still checked"
    assert qp.problems(["SCAN il2ks_db_tour", "USE TEMP B-TREE FOR ORDER BY"], limited) == []
    assert qp.problems(["CO-ROUTINE subquery", "SCAN subquery"]) == []


FIXTURE_MISSIONS = {
    "typical": "2026-09-01_10-00-00",
    "most_bailouts": "2026-09-02_10-00-00",
    "two_mission_ends": "2026-09-03_10-00-00",
    "bailout_ejection_stale_wheels": "2026-09-04_10-00-00",
}


def test_ingest_queries_use_an_index(big_world: SeededWorld, tmp_path: Path) -> None:
    """Ingesting fixture missions into the seeded world: its reads and WHERE-ed updates hit tables that are large on a
    real server (looking players up by account, a mission's rows by mission, the recompute of the level-2 tables)."""
    source = tmp_path / "import"
    source.mkdir()
    for name, uid in FIXTURE_MISSIONS.items():
        shutil.copy(FIXTURE_LOGS / f"{name}.txt.zip", source / f"missionReport({uid})[0].txt.zip")
    cfg = make_config(tmp_path / "data", None, after_archive="keep")

    summary, statements = capture(
        lambda: ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=source)), require_where=True
    )

    assert summary.failed == []
    assert statements
    blocks = failures_of("ingest", statements, "ingest")
    assert not blocks, f"{len(blocks)} ingest statement(s) cannot use an index:\n\n" + "\n\n".join(blocks)
