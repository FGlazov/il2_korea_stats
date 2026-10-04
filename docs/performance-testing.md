# Performance testing

Notes for developers. The public pages should feel fast (NFR-PERF-2, roughly under 300 ms) on the data a real server
has. Four tools catch regressions at different levels; all work on Windows and Linux with `uv` only (Node, k6 or
Lighthouse are not needed).

| Question | Tool | Where | Runs in CI |
|---|---|---|---|
| Did a page get slower or start running more queries? | server timing and query budgets | `tests/perf/` (`pytest -m perf`) | every test job |
| Did a page get heavier, or start blocking rendering? | page weight, request and render-blocking budgets | `tests/e2e/test_frontend_performance.py` | `test-e2e` job |
| Does the browser see a slow paint or a jumping layout? | LCP, CLS, TBT via `PerformanceObserver` | same file | `test-e2e` job |
| What happens with many visitors at once? | Locust | `loadtest/` | manual only (`Load test` workflow) |

## 1. Server timing and query budgets (`tests/perf/`)

```
uv run pytest -m perf                 # only these
uv run pytest -m "not perf"           # everything else (they also run in a plain `uv run pytest`)
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

## 2. Front-end budgets and web vitals (`tests/e2e/test_frontend_performance.py`)

Needs the e2e setup (`uv run playwright install chromium`, then `IL2KS_TEST_E2E=1 uv run pytest -m e2e`):

```
IL2KS_TEST_E2E=1 uv run pytest tests/e2e/test_frontend_performance.py
IL2KS_TEST_E2E=1 IL2KS_PERF_REPORT=1 uv run pytest tests/e2e/test_frontend_performance.py -s   # print numbers
```

Deterministic (no timing involved; they only change when a page, stylesheet or script changes):

- **page weight**: for each page of the e2e world, with a cold cache and waiting for the network to go quiet (htmx
  fragments included): number of requests, and decoded bytes of HTML, CSS, JS, fonts, images and in total. Numbers are
  uncompressed (the dev server doesn't compress; WhiteNoise does in production, roughly a quarter of the size).
- **no third-party requests**: everything comes from our own origin (no CDN, web fonts or analytics).
- **render-blocking**: in the `<head>`, only the theme script (`theme-init.js`, it must run before first paint) may block;
  at most 4 stylesheets; no inline scripts.

Timing (generous limits, they only catch big regressions): **LCP** (2.5 s), **CLS** (0.1, today about 0.001),
**TBT** from long tasks (300 ms, today 0). The limits are Google's "good" thresholds, loosened for TBT.

When a budget fails, the message says which number and by how much. If the growth is intended, raise the constant at
the top of the test file in the same commit. Lighthouse was left out on purpose: it needs Node and Chrome flags in CI and
its score is noisy, while these checks read the same Performance API numbers directly.

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
