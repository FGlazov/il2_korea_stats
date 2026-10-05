# Performance testing

Notes for developers. The public pages should feel fast (NFR-PERF-2, roughly under 300 ms) on the data a real server
has. Five tools catch regressions at different levels; all work on Windows and Linux with `uv` only (Node, k6 or
Lighthouse are not needed).

| Question | Tool | Where | Runs in CI |
|---|---|---|---|
| Did a page get slower or start running more queries? | server timing and query budgets | `tests/perf/` (`pytest -m perf`) | own CI job `test-perf` (SQLite, then Postgres; the plan test skips itself there); the other test jobs run `-m "not perf"` |
| Can every query use an index? | query-plan test (`EXPLAIN QUERY PLAN`) over every page variant and the ingest | `tests/perf/test_query_plans.py` | same `test-perf` job (SQLite run) |
| Did a page get heavier, or start blocking rendering? | page weight, request and render-blocking budgets | `tests/e2e/test_frontend_performance.py` | `test-e2e` job |
| Does the browser see a slow paint or a jumping layout? | LCP, CLS, TBT via `PerformanceObserver` | same file | `test-e2e` job |
| What happens with many visitors at once? | Locust | `loadtest/` | manual only (`Load test` workflow) |

## 1. Server timing and query budgets (`tests/perf/`)

`uv run il2ks dev check --full` runs them too (in one process: the world is seeded once, and the timings are steadier).

```
uv run pytest -m perf                 # only these
uv run pytest -m "not perf"           # everything else (they also run in a plain `uv run pytest` locally; CI keeps them out of the functional jobs)
IL2KS_PERF_REPORT=1 uv run pytest -m perf -s    # print the measured medians
```

A module-scoped fixture (`tests/perf/conftest.py`) seeds 60 missions with 30 sorties each (1,800 sorties, 250 players)
with the factories and the real `save_mission` (`tests/perf/seed.py`), inside a transaction that is rolled back at the end,
so other tests never see it. Seeding takes about 40 s once per run. Lists paginate and the heaviest player has real history.

For every public page (`tests/perf/pages.py`):

- **query budget at scale**: `assert_simple_reads` with a tight budget (what the page runs today, plus one). The data is 30
  times bigger than in the page tests, so a page that runs a query per row (N+1) fails here even when it passes there.
- **full render time** (cache disabled: a plain GET): the median of 7 requests after a warm-up, against a generous budget
  (400 ms; 600 ms for the mission page; the real numbers are 5 to 30 ms), so a slow CI runner doesn't flake it, and only an
  order-of-magnitude regression fails.
- **revalidation** (cache enabled: `If-None-Match`, TD-28): must be a 304 with exactly one query and under 100 ms.

`test_every_public_page_has_a_budget` fails when a URL name has no row in `tests/perf/pages.py`: **a new public page needs a
row there** (URL builder, query budget, optionally a time budget). Raise a budget only on purpose, in the same commit
as the change that needs it, and say why.

## Query plans: every query can use an index (`tests/perf/test_query_plans.py`)

The site is read-heavy, so an index costs little and a scan of a table that grows with play costs a lot. The rule: every
SELECT a page runs (and every SELECT / UPDATE / DELETE **with a WHERE** the ingest runs) must be answerable through an
index. Runs on SQLite (`EXPLAIN QUERY PLAN`) and on Postgres (`IL2KS_TEST_DB=postgres`, `EXPLAIN (FORMAT JSON)`): Postgres
plans from statistics and would pick a seq scan on a few thousand seeded rows whatever the indexes, so the test sets
`enable_seqscan` and `enable_sort` off (`SET LOCAL`: they add a huge cost, they do not forbid). A seq scan or a Sort node left in
the plan then means no index can serve the statement. The Postgres run takes ~5 min.

```
uv run pytest tests/perf/test_query_plans.py            # ~2 min: it seeds its own world (60 missions, like the timing tests)
```

What is checked (`tests/perf/query_plans.py`):

- **Pages**: every row of `PAGES` plus the filter and sort variants of `tests/perf/plan_variants.py` (every `?sort=` key of
  every list, ascending and descending, each board with `?tour=`, `?pool=`, `?aircraft=`, the mission and sortie filters).
  The variants are built from the same whitelists the views use, so a new sort key or board is picked up by itself. A new
  page needs a row in `PAGES` (an existing test makes you) and gets its default variant for free.
- **Ingest**: the four fixture missions are ingested into the seeded world and every statement is checked the same way.
- **Scan**: `SCAN <table>` without `USING INDEX` / `USING COVERING INDEX` reads every row. It fails for every table that is
  not in `SMALL_TABLES`. Tables are read from the plan, so **a table added tomorrow is checked without touching the test**.
  (`SCAN t USING INDEX i` walks an index in order: that is how `ORDER BY ... LIMIT` is answered without a sort, so it passes;
  a `COUNT(*)` over most of a table passes through a covering index.)
- **Sort**: `USE TEMP B-TREE FOR ORDER BY` on a statement with a `LIMIT` sorts everything to return a page. Only
  statements with a LIMIT are checked (without one every row is returned anyway). The default sort of every list, and the
  few common ones, must be served by an index; the other `?sort=` columns of a list are *rare sorts* (a visitor clicking a
  column header): they may sort the scope (`Variant.rare_sort`, see `PLAYER_LIST_INDEXED` and the `indexed` tuples).

Every table of the app must be read by some checked plan or be listed in `NOT_PLANNED_TABLES` with a reason (the last test
of the file; it skips when the file is split over xdist workers). A table that is only written needs that entry.

### Reading a failure

```
/leaderboards/air/?tour=all
  SQL: SELECT ... FROM "il2ks_db_player" WHERE (NOT ... "sorties" >= %s) ORDER BY "score_air" DESC, "name_lower" ASC ... LIMIT 20
  plan:
    SEARCH il2ks_db_player USING INDEX player_list_sorties (sorties>?)
    USE TEMP B-TREE FOR ORDER BY
  -> SORT of il2ks_db_player: USE TEMP B-TREE FOR ORDER BY
```

The first line is the page (or `ingest`), then the statement without its column list, its plan (indented by nesting) and
what is wrong. `SCAN of <table>`: no index serves the WHERE. `SORT of <table>`: the table the plan starts from has no
index in the order of the ORDER BY. Fix it with an index in `Meta.indexes` (and a new migration: never edit an applied one):

- the columns in the order of **equality filters, then the ORDER BY columns** (with their `-` direction: SQLite reads an
  index backwards, but not with one column ascending and the next descending);
- add the other WHERE columns **after** the ORDER BY columns: the filter is then answered from the index, and the
  COUNT of the pagination becomes a covering-index scan;
- watch the planner: with no statistics SQLite may pick another index for a range filter (`sorties >= 5`) over the one that
  gives the order. If it does, move the filter column behind the order columns or drop the competing index.

### Allowing something, with a reason

Only where no index can help, and always with a reason (a test fails on an empty one or a stale table name):

- `SMALL_TABLES` `{table: reason}`: a table that stays tiny (settings, tours, the game-object catalog, per-aircraft-type
  stats). A table that grows with play never belongs here.
- `SCAN_ALLOWANCES` / `SORT_ALLOWANCES`: `QueryAllowance(table, sql_contains, reason, url_prefix)` for one kind of
  statement (matched on the table the plan starts from, a snippet of the SQL, and optionally the page: `ingest` for the
  ingest). Today: the player-name substring search, the leaderboards' tie-break on the joined player name, the per-hour
  boards' ratio sort, the Elo replay, the threshold percentiles. Read their reasons before adding one.
- A rare sort column of a list: do nothing; it is a rare sort unless you put it in the list's `indexed` tuple (then it must
  have an index).

## 2. Front-end budgets and web vitals (`tests/e2e/test_frontend_performance.py`)

Needs the e2e setup (`uv run playwright install chromium`, then `IL2KS_TEST_E2E=1 uv run pytest -m e2e`):

To reuse a server you already started, set `IL2KS_E2E_BASE_URL=http://127.0.0.1:PORT` (opt-in, local only; unset = the suite starts its own server, as CI does). That server must serve the seeded e2e world (`python -m tests.e2e.world` into its data dir), because the tests assert its names and figures.

```
IL2KS_TEST_E2E=1 uv run pytest tests/e2e/test_frontend_performance.py
IL2KS_TEST_E2E=1 IL2KS_PERF_REPORT=1 uv run pytest tests/e2e/test_frontend_performance.py -s   # print numbers
```

Deterministic (no timing involved; they only change when a page, stylesheet or script changes):

- **page weight**: for each page of the e2e world, with a cold cache and waiting for the network to go quiet (htmx
  fragments included): number of requests, and decoded bytes of HTML, CSS, JS, fonts, images and in total. Numbers are
  uncompressed (the dev server doesn't compress; WhiteNoise does in production, roughly a quarter of the size). The e2e
  server is `il2ks web --dev`, which serves the readable CSS/JS; production minifies our own files first (`collectstatic`,
  doc 16): own CSS 82 to 59 KB and own JS 17 to 9.7 KB (site.css 54 to 41 KB), so production pages weigh about 30 KB
  less than the budgets measure; the budgets stay as they are.
- **no third-party requests**: everything comes from our own origin (no CDN, web fonts or analytics).
- **render-blocking**: in the `<head>`, only the theme script (`theme-init.js`, it must run before first paint) may block;
  at most 4 stylesheets; no inline scripts.

Timing (generous limits, they only catch big regressions): **LCP** (2.5 s), **CLS** (0.1, today about 0.001),
**TBT** from long tasks (300 ms, today 0). The limits are Google's "good" thresholds, loosened for TBT.

When a budget fails, the message says which number and by how much. If the growth is intended, raise the constant at
the top of the test file in the same commit. Lighthouse was left out on purpose: it needs Node and Chrome flags in CI and
its score is noisy, while these checks read the same Performance API numbers directly.

## Import benchmark (`il2ks dev bench-ingest`)

Times an import of a log folder split by phase (group, parse, replay, persist level 1, level 2, ratings, archive), for
checking that a change to the ingester did not make it slower:

```
uv run il2ks dev bench-ingest <folder with .txt / .txt.zip logs> [--limit N] [--cpu] [--profile out.prof] [--data-dir DIR]
```

It works on a copy of the logs in a throw-away data directory, so your real data and the originals are never touched.
`--limit N` takes only the first N missions, `--cpu` measures process CPU time instead of wall-clock time (steadier on a
busy machine), `--profile FILE` writes a cProfile dump of the whole run, and `--data-dir DIR` keeps the resulting database
(the folder must be new or empty). To prove that a speed-up changed no data, keep the data directory of a run before and
after the change and compare them:

```
uv run il2ks dev dump-db <data dir> before.jsonl      # every table, sorted by primary key, one JSON object per line
```

then `diff before.jsonl after.jsonl` (columns that hold the time of the import itself are left out). A smoke test
(`tests/integration/test_dev_bench.py`) runs both commands on the small fixture logs.

### Timing one full-tour refresh (`aggregates.refresh_tours`)

Every level-2 refresh redoes the touched tours completely, so its cost grows with the size of the tour (maintainer
2026-10-05: 5 minutes per mission would still be fine, 10 seconds is the target). To measure the worst case, ingest every
sample mission into ONE monthly tour in a throw-away data directory (copy the logs out of `sample_data/` first, never
ingest from it), keep the data directory (`bench-ingest --data-dir`), and time `refresh_tours` on it in a script:

```
uv run il2ks dev bench-ingest <copy of the logs> --cpu --data-dir <new dir>
# then, with IL2KS_DATA_DIR=<that dir> and django.setup():
#   with transaction.atomic(): aggregates.refresh_tours({tour_pk}, DEFAULT_RULES)     # repeat 3 times, take the median
```

The refresh is idempotent, so repeating it on the same database measures the same work (every row is compared, none is
written). What to look at: wrap the steps of `aggregates` (`_recompute_tour_scope`, `_recompute_all_time`, ...) for the
per-step split; `cProfile` for the Python side; a `connection.execute_wrapper` for the number of queries. Time the rows
a query returns, too: SQLite computes lazily while Python fetches, so `execute` alone hides half of the query time. For
Postgres see the notes at the top of `tests/conftest.py` (`IL2KS_TEST_DB=postgres`, `IL2KS_PG_PORT`, `IL2KS_PG_NAME`:
a database of its own, never one that other jobs use).

## 3. Load test with Locust (`loadtest/`)

Locust is the right tool here: it is pure Python (installed with the dev group, nothing else to download), the flows are
ordinary Python, and it has a headless mode and a web UI. `k6` needs its own binary and JavaScript flows; `wrk`/`ab`
hit one URL, not a visitor's journey. Locust does not run in the default CI: results on a shared runner vary too much
to gate a commit on, and a load run takes minutes.

One command (seeds a scratch database, starts `il2ks web --dev` on a free port, runs Locust, stops everything):

```
uv run python loadtest/run.py                       # 20 users, 60 s, fresh seed
uv run python loadtest/run.py -u 50 -t 3m --html report.html
uv run python loadtest/run.py --data-dir <dir>      # reuse a data dir seeded earlier (skips the minute of seeding)
```

Or by hand, against any `il2ks web` you started yourself (a copy of a real database works too, because the test
crawls the lists for ids to visit; **never load-test somebody else's server**):

```
IL2KS_DATA_DIR=<scratch dir> uv run python -m tests.perf.seed 60      # missions (optional: players, sorties per mission)
IL2KS_DATA_DIR=<scratch dir> uv run il2ks web --dev --port 8765
uv run locust -f loadtest/locustfile.py --host http://127.0.0.1:8765          # web UI on http://localhost:8089
uv run locust -f loadtest/locustfile.py --host http://127.0.0.1:8765 --headless -u 20 -r 10 -t 60s
```

The visitors (`loadtest/locustfile.py`): a **Browser** (home, missions, a mission, one of its sorties), a **Searcher**
(search, profile, the player's sorties, a sortie), an **Aircraft fan** (aircraft list, a type, streaks), a **Watcher**
(polls the `/live/` fragment like an open home page) and a **Returning** visitor (conditional GETs that must be 304).
Each new visitor also fetches the static files once. Endpoints are grouped by route (`/players/[id]/`), so the report has
one line per page type.

The run ends with a short table you can paste into an issue:

```
| endpoint | requests | fails | RPS | p50 ms | p95 ms | max ms |
| /missions/[id]/ | 74 | 0 | 2.5 | 93 | 570 | 625 |
| ...
| TOTAL | 1109 | 0 | 37.9 | 17 | 160 | 625 |
```

The exit code is 1 when more than 1% of the requests fail. `--dev` uses Django's development server (threaded, no
compression), so absolute numbers are pessimistic: compare **runs of the same setup before and after a change**, don't
read them as capacity. For a production-like run, start `il2ks web` on a seeded data dir with the dev settings of your
config and point `--host` at it.

On GitHub: Actions, "Load test", "Run workflow" (users, duration, missions as inputs); the table and Locust's HTML report
are attached to the run as an artifact.

## Adding a page

1. A row in `tests/perf/pages.py` (the completeness test makes you).
2. If the page is in the e2e world's `PUBLIC_PAGES` (`tests/e2e/test_smoke.py`), it is covered by the front-end budgets.
3. If visitors reach it often, a step in a flow of `loadtest/locustfile.py`.
