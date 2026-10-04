# 08 — Development Workflow

## Environment `[DECIDED]` (uv) / `[PROPOSED]` (rest)

```
uv sync                          # create .venv, install deps (incl. dev group)
uv run il2ks setup --dev         # write dev config (SQLite by default), migrate
uv run il2ks ingest --from sample_data/2026-09   # load local sample logs (FR-ING-13)
uv run pytest                    # all tests on SQLite (default, zero setup)
uv run pytest tests/unit         # fast core tests only, no DB

docker compose -f docker/compose.dev.yaml up -d db   # Postgres for dev/tests
IL2KS_TEST_DB=postgres uv run pytest                 # same suite on Postgres
IL2KS_DATABASE_URL=postgres://... uv run il2ks run   # run the app on Postgres

uv run ruff check --fix && uv run ruff format
uv run pyright                    # strict mode (TD-12)
uv run il2ks run                 # web + watcher locally
```
- **Two databases, both first-class** (TD-04, TD-19). SQLite is the zero-setup default. Postgres comes from Docker or a native
  local install. **SQLite is the default for every test run** (local, hooks, main CI jobs). Postgres is opt-in (`IL2KS_TEST_DB=postgres`),
  and CI runs it in one extra job. The DB-portability tests (migrations from zero on both backends, SQLite → Postgres
  copy with identical query results) live in `tests/integration/test_db_portability.py`.
- Dependencies are split into groups in `pyproject.toml`: runtime, `dev` (pytest, ruff, pyright, import-linter, pre-commit).
- Old Python on PATH: this machine's `python` is 3.5 (left over from `il2_stats`). Always go through `uv run`.

## Sample data and fixtures
- `sample_data/` holds real server logs (gitignored, **never commit**). Use it for local exploration, performance tests,
  and an opt-in "big" test run (`pytest -m sample_data`, skipped when the folder is missing, so CI and other contributors still pass).
- Committed fixtures in `tests/fixtures/logs/` are **anonymized excerpts or whole missions**: nicknames → `Player-<hash>`,
  UUIDs → fake UUIDs from a keyed hash (HMAC with a secret, gitignored key), so the same player has the same fake ID in every fixture
  and nobody can re-identify a player by hashing a known UUID. A script (`il2ks dev anonymize`) does this, and a test checks fixtures for real-looking
  UUIDs and known nicknames.

## Testing strategy `[DECIDED]` (unit test where possible) / `[PROPOSED]` (layers)

The maintainer will "vibe-code" a lot, so **tests are the main guardrail**. Every bug fix gets a
regression test first.

| Layer | What | Speed | Notes |
|---|---|---|---|
| Parser unit tests | Each event type: a valid line → the expected dataclass. Malformed, truncated, and unknown lines → a warning, not an exception. | ms | Parametrized tables of `(line, expected)` |
| Replay scenario tests | Small hand-written logs (10–50 lines) for one rule each: "took off, shot down by player B, bailed out", "landed at friendly airfield", "disconnected mid-flight", "friendly fire", "killed by AI". | ms | Readable, one scenario per test. This is the rulebook in executable form |
| Streaming equivalence | Feeding a fixture mission event by event, then calling `finish()`, gives exactly the same `MissionResult` as `run()`. Snapshots taken mid-mission never raise. | <1 s | Guards the live path (TD-07) |
| Golden snapshot tests | Full real mission logs → `MissionResult` serialized to JSON, compared with a committed snapshot (for example `syrupy`). Any behavior change shows up as a reviewable diff. | <1 s each | **Anonymize** player names and UUIDs in committed fixtures (privacy). Provide a script to do it |
| Property / fuzz tests (optional) | `hypothesis`: shuffled or garbage input never crashes the parser. | s | Nice to have |
| Ingest integration | Real Postgres. Ingest fixture logs, then assert row counts and key facts. Ingesting twice is idempotent. A failing mission rolls back. | s | pytest-django with a Postgres test DB |
| Query tests | `queries/*` functions return correct totals for the fixture data. | s | |
| View smoke tests | Every URL returns 200 against the fixture DB and contains key text. HTMX endpoints return fragments. Each view stays under a query budget and its captured SQL has no `GROUP BY` or aggregates (TD-22). | s | Catches template errors and creeping read-time aggregation |
| Aggregate rebuild tests | Incrementally built level-2 tables equal a full `rebuild-aggregates` from level 1 (TD-08). | s | Guards against counter drift, the old system's main bug class |
| Sample-data distribution checks (opt-in, `-m sample_data`) | Ingest all of `sample_data/` and assert distributions stay in expected bands: bailouts 10–15% of sorties that took off (12.6% now), payload resolution ≥ 99%, "mission ended" around 10%, unknown outcomes below a threshold per aircraft type. Plain pytest asserts, no pandera. | minutes | Catches rule or parser changes that shift totals even when single rows look fine. Skipped when `sample_data/` is missing |
| DB constraint tests | Writing rows that violate a model constraint fails (constraints exist and are migrated on both backends). | s | |
| Override tests | A template in a temporary `custom/` folder overrides the built-in one. Branding settings show up in the page CSS (TD-25). | s | |
| Page performance (`-m perf`, `tests/perf/`) | A seeded world of ~1,800 sorties (60 missions, 250 players, real `save_mission`, rolled back at the end). Per public page: a tight query budget (N+1 guard), the median of 7 full renders against a generous time budget, and a 304 revalidation with exactly one query. A completeness test fails when a public URL name has no row in `tests/perf/pages.py`. On Postgres the seeded database gets **`ANALYZE`** after seeding (stale planner statistics once made the mission page take 59 s there while SQLite took 30 ms). Details: `docs/performance-testing.md` (still says "every test job"; stale). | ~40 s seeding per run | `[PROPOSED]` (2026-10-04). Runs in **its own CI job** `test-perf`, SQLite then Postgres, while the functional jobs skip it with `-m "not perf"` so a slow runner never fails them (OQ-97, maintainer); `il2ks dev check --full` runs it too |
| Front-end performance | `tests/e2e/test_frontend_performance.py` (opt-in e2e): page weight, request count, no third-party requests, render-blocking budgets, LCP / CLS / TBT (NFR-PERF-6). Locust in `loadtest/`, manual `workflow_dispatch` job only. | slow | `[PROPOSED]` |
| End-to-end | Playwright (Chromium) against the real `il2ks web --dev` on a synthetic world (factories + one anonymized fixture log through the real ingest): smoke tests for every page, dark mode, console errors; flows "player finds own sortie" and "mission → myself → sortie". Opt-in: `IL2KS_TEST_E2E=1 uv run pytest -m e2e` (once: `uv run playwright install chromium`); CI job `test-e2e`. | slow | `[PROPOSED]` (built 2026-10-03 at the maintainer's request; flow tests are marked pending until all pages are merged) |

The coverage target applies to `core/`: at least 90%, enforced in CI. Elsewhere coverage is just reported.

## Regression guards and the one gate `[PROPOSED]` (2026-10-04)

`uv run il2ks dev check` is the one gate: agents, the Stop hook, pre-commit and CI all run it. Tiers: `--fast` (about 30 s: no unit tests), the default
(quick: plus the unit tests), `--full` (plus integration/page tests, which carry the query budgets, **and `tests/perf`**, about 1-2 more minutes); `--postgres`, `--e2e`, `--fix` (applies the
auto-fixable parts). The page query budgets live as shared constants in `tests/simple_reads.py` (`PROFILE_READS_ALL_TIME` 14, `PROFILE_READS_TOUR` 15, `HOME_READS` 12, `HOME_READS_EMPTY` 11); raise one only with a reason in the test, and run `--full` after merging because budgets drift when features merge. `CLAUDE.md` tells agents to add `--postgres` when they touch models, migrations or a query. Guards beyond lint, format, pyright, import-linter and vulture:
- **Released migrations are frozen**: one that exists on `origin/main` or `main` can't be modified, renamed or deleted; there must be exactly one
  leaf and no duplicate numbers (`il2ks dev check-migrations`); `makemigrations --check` must be clean. Maintainer override
  `IL2KS_ALLOW_RELEASED_MIGRATION_EDIT=1` (planned use: squashing the migrations once before the first release).
- `bump-templates --check` (template/CSS/JS versions, TD-25), `translations check`, every template compiles (`test_template_compile`), and the
  workflow files validate against the GitHub schema (`workflows` step).
- `pytest-xdist` parallelises the suite: the full run takes about 2 min (was about 11).
- Hooks in `.claude/settings.json`: PreToolUse blocks edits of released migrations and of `sample_data/` and risky git commands; PostToolUse
  formats; Stop runs `check --fast` when something changed. The `merge-branch` skill is the merge procedure, `watch-ci` follows a push.

## CI `[PROPOSED]`
GitHub Actions on push and PR: ruff, pyright, import-linter, pytest (unit + integration), in this matrix:
**SQLite on ubuntu and windows** (most target hosts run Windows) **plus Postgres on ubuntu** (as a service container), a **`test-perf` job** (`pytest -m perf`, SQLite then Postgres, OQ-97), a `test-e2e` job (Playwright) and the separate **Windows installer** workflow (doc 07). The repo goes **public** within days of 2026-10-02, so GitHub-hosted Actions minutes are free and unmetered. The matrix size isn't a cost concern. Later: build Docker image and installer artifacts on tags.

## Working with Claude Code

The maintainer asked whether to use Claude skills or something else. Recommendation, in priority order:

1. **`CLAUDE.md` at the repo root** (highest impact, do it first). Keep it short: what the project is, the
   commands above, the layer rule ("core/ never imports django"), "read design_doc/ before architectural changes
   and update it after", "every bug fix gets a regression test", naming conventions, and where things live.
   Claude reads it automatically every session.
2. **Hooks** in `.claude/settings.json`. These are deterministic: the harness runs them, so the model can't forget.
   - After edits: run `ruff format` and `ruff check --fix` on the changed file.
   - Before finishing a turn (Stop hook): run the fast unit tests (`pytest tests/unit -q`) and report failures back.
   - This turns "please remember to lint and test" into an enforced rule.
3. **Tests + CI** (above). These are the real safety net, whatever tool wrote the code.
4. **Project skills** (`.claude/skills/`), added once a workflow repeats. Good candidates:
   - `add-log-event`: a new event type end to end (dataclass, regex, parser test, replay handler, scenario test).
   - `add-page`: a new page (query function, view, template, URL, smoke test) following conventions.
   - `update-object-catalog`: add new aircraft or objects after a game patch.
   - `golden-update`: regenerate snapshot tests and summarize the behavior diff for review.
   Skills are packaged instructions loaded on demand, so they keep `CLAUDE.md` small. Write one after doing a task twice, not before.
   - **Exists now:** `design-doc-sync` (`.claude/skills/design-doc-sync/`). It checks `design_doc/` for changes since the last review
     (including uncommitted edits by the maintainer) before design-relevant work, and writes decisions back afterwards.
5. **Review**: use `/code-review` on branches before merging, and a `/security-review` pass before releases.

## Conventions `[PROPOSED]`
- English everywhere. **Type hints mandatory and tight** (TD-12): no missing annotations, no `Any`, `Literal`/`Enum`/`NewType` where they fit. Use dataclasses or Django models; no bare dicts across layer boundaries.
- Requirement IDs (`FR-WEB-6`) and decision IDs (`TD-08`) are referenced in docstrings and PR descriptions where relevant.
- Small modules. If a file goes past roughly 400 lines, consider splitting it (the old loader was 800+ lines in one function chain).
