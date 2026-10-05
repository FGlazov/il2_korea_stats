"""Query-plan checks: every SELECT (and UPDATE/DELETE with a WHERE) a page or the ingest runs must be able to use an
index (maintainer request 2026-10-04: we are read-heavy, one more index costs little). The plan comes from
`EXPLAIN QUERY PLAN` on SQLite, `EXPLAIN (FORMAT JSON)` with seq scans and sorts made expensive on Postgres. How to
read a failure and how to allow-list: docs/performance-testing.md, section "Query plans".

The rule, on the plan of one statement:

- **scan**: `SCAN <table>` with no `USING INDEX` / `USING COVERING INDEX` reads every row of the table. Fails for any
  table that is not in `SMALL_TABLES`. (`SCAN t USING INDEX i` walks an index in order: it is how SQLite answers
  `ORDER BY ... LIMIT` without sorting, so it passes.) Tables are found from the plan, so a table added tomorrow is
  checked without touching this file.
- **sort**: `USE TEMP B-TREE FOR ORDER BY` on a statement that reads a big table sorts the whole result before the
  page is cut. Fails for a page's default order; for a sort the visitor picks it needs a `SORT_ALLOWANCES` entry.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, cast

from django.db import connection

type SqlParam = str | int | float | bool | None

SMALL_TABLES: dict[str, str] = {
    "il2ks_db_sitesettings": "one row of site settings",
    "il2ks_db_navlink": "a few navigation links the owner configured",
    "il2ks_db_tour": "one row per season: a few dozen at most",
    "il2ks_db_gameobject": "the catalog of aircraft, vehicles and objects: a few hundred rows, written at ingest only",
    "il2ks_db_country": "the country catalog: a few dozen rows",
    "il2ks_db_statthreshold": "a dozen metrics per tour: a few hundred rows at most",
    "il2ks_db_dataversion": "one row per data migration step",
    "il2ks_db_livemission": "one row per running server",
    "il2ks_db_liveplayer": "players online right now: dozens of rows",
    "il2ks_db_reprocessrequest": "a handful of owner-triggered reprocess requests, deleted when done",
    "il2ks_db_aircraftstats": "one row per aircraft type flown (catalog-sized: dozens to a few hundred)",
    "il2ks_db_aircraftammostats": "one row per aircraft type and ammunition type (catalog-sized, a few hundred)",
    "il2ks_db_achievementholders": "one row per medal tier somebody holds (about a hundred)",
}
"""`{table: reason}`: tables a full scan of which is fine. Everything else is a big table (it grows with every mission
or every player). A new entry needs a reason that says why the table stays small; a table that grows with play never
belongs here."""


NOT_PLANNED_TABLES: dict[str, str] = {}
"""`{table: reason}`: tables no checked page or ingest query reads (only written, or read by something the test cannot
reach). The coverage test lists every other table that no plan touched, so a new table cannot dodge the check."""


@dataclass(frozen=True)
class QueryAllowance:
    """A scan or sort that no index can fix, for one kind of query. Matched on the big table the plan starts from, the
    SQL text and, when given, the page (the ingest's statements have the url `ingest`)."""

    table: str  # the big table concerned
    sql_contains: str
    reason: str
    url_prefix: str = ""


_JOIN_TIE_BREAK = (
    "the leaderboard order is score, then the player's name: the tie-break column lives in the joined `player` table, "
    "which no index of this table can supply, so SQLite sorts the rows of ONE scope (a tour, an aircraft type or a "
    "propulsion pool: the pilots active in it). An index would only help once the tie-break is the player id (a "
    "product decision: ties would stop being alphabetical)"
)
_PER_HOUR = (
    "a per-hour board orders by a ratio of two stored columns (amount / seconds): no portable index serves an "
    "expression with a bound constant, so the (scoped) rows that passed the minimum are sorted; the covering filter "
    "index keeps the read itself a range scan"
)

SCAN_ALLOWANCES: tuple[QueryAllowance, ...] = (
    QueryAllowance(
        "il2ks_db_playername",
        "LIKE",
        "the player search is a substring match ('%query%'): a B-tree index cannot serve a leading wildcard (on "
        "Postgres it would need a trigram index, on SQLite FTS); PlayerName has about one row per player",
        "/players/?q=",
    ),
    QueryAllowance(
        "il2ks_db_kill",
        "OR T",
        "the ingest's pair, type and aircraft recomputes read the kills of the pilots that flew (killer OR victim): "
        "the OR spans two joined sortie tables, which one index cannot serve (a rewrite as two queries would)",
        "ingest",
    ),
    QueryAllowance(
        "il2ks_db_kill",
        'ASC, "il2ks_db_kill"."time"',
        "the Elo replay: every qualifying kill, oldest first, because ratings depend on the order of the games "
        "(`ingest.ratings`); the same code path as a full rebuild",
        "ingest",
    ),
    QueryAllowance(
        "il2ks_db_playersortie",
        '"elo_peak" > %s',
        "the Elo replay's write-back reads the stored `elo_peak` of every rated sortie to write only the ones that "
        "changed: the rows with an Elo are most of the table by definition (`ingest.ratings`)",
        "ingest",
    ),
    QueryAllowance(
        "il2ks_db_player",
        '>= %s OR "il2ks_db_player"',
        "the stat thresholds (percentiles) are computed over every eligible pilot: a whole-population pass by "
        "definition (`ingest.stat_marks`)",
        "ingest",
    ),
)
"""Per-query exceptions for scans (the table must be a big one; a small one belongs in `SMALL_TABLES`)."""

SORT_ALLOWANCES: tuple[QueryAllowance, ...] = (
    QueryAllowance("il2ks_db_playertour", '"name_lower" ASC', _JOIN_TIE_BREAK),
    QueryAllowance("il2ks_db_playertouraircraft", '"name_lower" ASC', _JOIN_TIE_BREAK),
    QueryAllowance("il2ks_db_playertourpool", '"name_lower" ASC', _JOIN_TIE_BREAK),
    QueryAllowance("il2ks_db_playeraircraft", '"name_lower" ASC', _JOIN_TIE_BREAK),
    QueryAllowance("il2ks_db_playerpool", '"name_lower" ASC', _JOIN_TIE_BREAK),
    QueryAllowance("il2ks_db_player", '"kills_intercept" * %s) /', _PER_HOUR),
    QueryAllowance("il2ks_db_player", '"score_ground_attack" * %s) /', _PER_HOUR),
    QueryAllowance("il2ks_db_player", '"kills_tank_attack" * %s) /', _PER_HOUR),
    QueryAllowance(
        "il2ks_db_playername",
        "LIKE",
        "the sort of a substring search (see its scan): the matching name rows are ordered by the player's column",
        "/players/?q=",
    ),
    QueryAllowance(
        "il2ks_db_playertour",
        '"il2ks_db_tour"."started_at" DESC',
        "a player's tour history for the profile charts: the rows of one player, at most one per tour played",
    ),
    QueryAllowance(
        "il2ks_db_playermission",
        '"il2ks_db_playermission"."kills_air" DESC',
        "the top pilots of one mission: the roster of one mission (at most a few hundred rows)",
    ),
)
"""Per-query exceptions for `TEMP B-TREE FOR ORDER BY` (only statements with a LIMIT are checked, see `problems`)."""

NOT_TABLES = {"subquery": "a FROM-subquery's materialised result (its own tables are in the plan lines above)"}

_SCAN = re.compile(r"^SCAN (?P<table>\w+)(?: AS \w+)?(?P<rest>.*)$")
_SEARCH = re.compile(r"^SEARCH (?P<table>\w+)")
ORDER_BY = re.compile(r"\bORDER BY\b")
HAS_LIMIT = re.compile(r"\bLIMIT\b")
SORT_MARK = "USE TEMP B-TREE FOR ORDER BY"


@dataclass(frozen=True)
class Problem:
    kind: str  # "scan" or "sort"
    table: str  # the scanned table; for a sort: a big table the statement reads
    detail: str  # the plan line
    tables: tuple[
        str, ...
    ] = ()  # for a sort: every big table the statement reads (the join order differs per database)


@dataclass(frozen=True)
class Statement:
    sql: str
    params: tuple[SqlParam, ...]


def explain(statement: Statement) -> list[str]:
    """The plan of one statement as text lines, one per plan node, indented by depth, in SQLite's vocabulary
    (`SCAN t`, `SCAN t USING INDEX i`, `SEARCH t USING INDEX i (..)`, `USE TEMP B-TREE FOR ORDER BY`), whatever the
    database: the rules below read that vocabulary only."""
    if connection.vendor == "postgresql":
        return _explain_postgres(statement)
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN QUERY PLAN " + statement.sql, statement.params)
        rows = cursor.fetchall()
    depth: dict[int, int] = {0: 0}
    lines: list[str] = []
    for node_id, parent, _unused, detail in rows:
        depth[node_id] = depth.get(parent, 0) + 1
        lines.append("  " * (depth[node_id] - 1) + str(detail))
    return lines


_PG_SEQ = "Seq Scan"
_PG_INDEXED = ("Index Scan", "Index Only Scan", "Bitmap Heap Scan")


def _explain_postgres(statement: Statement) -> list[str]:
    """Postgres plans from statistics, and on a seeded few thousand rows it prefers a seq scan over any index. So the
    planner is told seq scans and sorts are as bad as it gets (`enable_seqscan` / `enable_sort` off add a huge cost,
    they do not forbid): it then picks a seq scan only where no index can serve the query, and a Sort node only where no
    index delivers the order. The settings are transaction-local (`SET LOCAL`): nothing leaks to other tests."""
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute("SET LOCAL enable_sort = off")
        cursor.execute("EXPLAIN (FORMAT JSON) " + statement.sql, statement.params)
        row = cursor.fetchone()
    assert row is not None
    document = cast("list[dict[str, dict[str, Any]]]", row[0] if isinstance(row[0], list) else json.loads(row[0]))
    lines: list[str] = []
    _walk_postgres(document[0]["Plan"], 0, lines)
    return lines


def _walk_postgres(node: dict[str, Any], depth: int, lines: list[str]) -> None:
    kind = str(node["Node Type"])
    table = node.get("Relation Name")
    pad = "  " * depth
    if kind == _PG_SEQ and table:
        lines.append(f"{pad}SCAN {table}")
    elif kind.startswith(_PG_INDEXED) and table:
        index = node.get("Index Name")
        lines.append(f"{pad}SEARCH {table} USING INDEX {index or 'bitmap'}")
    elif kind == "Sort":
        lines.append(f"{pad}{SORT_MARK}")
    for child in node.get("Plans", []):
        _walk_postgres(child, depth + 1, lines)


def checkable(sql: str, *, require_where: bool = False) -> bool:
    """SELECT (with a WHERE when `require_where`: the ingest reads and rewrites whole tables on purpose, and a statement
    without a WHERE has nothing an index could narrow) and UPDATE / DELETE with a WHERE."""
    head = sql.lstrip().upper()
    if head.startswith("SELECT"):
        return " WHERE " in head or not require_where
    return head.startswith(("UPDATE", "DELETE")) and " WHERE " in head


def tables_read(plan: list[str]) -> list[str]:
    """The tables the plan reads, in plan order (the first is the one the statement starts from)."""
    found: list[str] = []
    for line in plan:
        match = _SCAN.match(line.strip()) or _SEARCH.match(line.strip())
        if match and match.group("table") not in found:
            found.append(match.group("table"))
    return found


def problems(plan: list[str], sql: str = "", *, rare_sort: bool = False) -> list[Problem]:
    """What is wrong with one plan, before any allowance is applied.

    A sort is only a problem when the statement has a LIMIT: without one every row is returned anyway, so ordering them
    costs what reading them costs. `rare_sort`: the visitor picked a sort column that no index serves (see
    `tests.perf.plan_variants`); the statement has to read and order the whole (scoped) table, so its scan and sort are
    accepted (a count or any statement without ORDER BY is still checked)."""
    if rare_sort and ORDER_BY.search(sql):
        return []
    found: list[Problem] = []
    for line in plan:
        text = line.strip()
        scan = _SCAN.match(text)
        if scan and "INDEX" not in scan.group("rest") and scan.group("table") not in SMALL_TABLES | NOT_TABLES:
            found.append(Problem("scan", scan.group("table"), text))
    if any(line.strip() == SORT_MARK for line in plan) and HAS_LIMIT.search(sql):
        big = [t for t in tables_read(plan) if t not in SMALL_TABLES | NOT_TABLES]
        if big:
            found.append(Problem("sort", big[0], SORT_MARK, tuple(big)))
    return found


def allowance_reason(problem: Problem, sql: str, url: str) -> str | None:
    """The reason this problem is accepted, or None."""
    pool = SCAN_ALLOWANCES if problem.kind == "scan" else SORT_ALLOWANCES
    for entry in pool:
        concerned = problem.kind == "sort" and entry.table in problem.tables
        if (
            (entry.table == problem.table or concerned)
            and entry.sql_contains in sql
            and url.startswith(entry.url_prefix)
        ):
            return entry.reason
    return None


def shorten(sql: str, limit: int = 300) -> str:
    """The statement without its column list: `SELECT ... FROM x WHERE ...`."""
    squashed = re.sub(r"\s+", " ", sql)
    squashed = re.sub(r"^SELECT .*? FROM ", "SELECT ... FROM ", squashed)
    return squashed if len(squashed) <= limit else squashed[: limit - 3] + "..."


def format_failure(where: str, sql: str, plan: list[str], found: list[Problem]) -> str:
    lines = [where, f"  SQL: {shorten(sql)}", "  plan:"]
    lines.extend(f"    {line}" for line in plan)
    lines.extend(f"  -> {p.kind.upper()} of {p.table}: {p.detail}" for p in found)
    return "\n".join(lines)
