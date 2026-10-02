# 04 — High-Level Architecture

## System context

```
 ┌──────────────────────┐   writes    ┌───────────────────────────┐
 │ IL-2 Korea DServer   │ ──────────► │ log directory (text logs) │
 └──────────────────────┘ (or copied └─────────────┬─────────────┘
                           from another machine, FR-ING-16)
                                                    │ polls / scheduled
                                                    ▼
                                      ┌───────────────────────────┐
                                      │ INGESTER (il2ks ingest)   │
                                      │ discover → parse → replay │
                                      │ → persist → archive       │
                                      └─────────────┬─────────────┘
                                                    │ one transaction per mission
                                                    ▼
                                      ┌───────────────────────────┐
                                      │ SQLite                    │
                                      │ (pre-aggregated tables)   │
                                      └─────────────┬─────────────┘
                                                    │ simple SELECTs only (TD-22)
                                                    ▼
 ┌──────────────┐  HTTPS   ┌────────────┐  HTTP    ┌───────────────────────────┐
 │ Browser      │ ◄──────► │ Caddy      │ ◄──────► │ WEB (Django + HTMX)       │
 │ (players)    │  :443    │ TLS, certs │ localhost│ granian, static files     │
 └──────────────┘          └────────────┘          └───────────────────────────┘
```

There are two application processes and one database `[DECIDED]`, plus the HTTPS proxy (TD-23). The ingester and the web app share the Django
models (the ORM), but they never call each other. The DB is their only shared state.

## Layers inside the codebase `[PROPOSED]`

The most important structural rule: **dependencies point inward, and the inner layers don't know
Django exists.** This fixes the biggest problem in the old code, where parsing, game logic, DB writes,
and aggregation were mixed together in a few huge functions.

```
           ┌─────────────────────────────────────────────┐
  outer    │ web/        Django views, templates, HTMX     │  reads DB via queries/
           │ ingest/     orchestration, file I/O, persist  │  writes DB via persist
           ├─────────────────────────────────────────────┤
           │ db/         Django models + migrations        │
           │ queries/    read-side query functions         │
           ├─────────────────────────────────────────────┤
  inner    │ core/logparse/  line → typed Event            │  pure Python
  (pure)   │ core/replay/    Events → MissionResult        │  no Django, no I/O
           │ core/catalog/   object/country/coalition data │
           └─────────────────────────────────────────────┘
```

| Layer | Responsibility | Depends on | Test style |
|---|---|---|---|
| `core.logparse` | Turn each log line into a frozen dataclass event (`TakeoffEvent`, `KillEvent`, and so on) with a generic key/value tokenizer, so unknown keys and ATypes are kept, not rejected (TD-20). Group log files into missions: raw `[N]` parts, or one concatenated archive (FR-ING-13). | stdlib only | Fast unit tests, one or more per event type, plus malformed-line tests |
| `core.replay` | An incremental state machine (`feed` / `snapshot` / `finish`, TD-07) that consumes an event stream and produces an immutable `MissionResult` (mission metadata, sorties, kills, damage, timeline). Provisional snapshots serve live views later. **All game rules live here**: kill credit, sortie outcome, bailout or capture, and so on. | `logparse`, `catalog` | Scenario tests (small synthetic logs) plus golden snapshot tests (real logs → expected JSON) |
| `core.catalog` | Static reference data: object names → class (fighter, bomber, AAA…), countries → coalitions. Default data ships in the package, and admin overrides live in the DB. | stdlib | Unit tests |
| `db` | Django models: pre-aggregated read models, level 1 per mission and level 2 across missions (TD-08), and migrations. | Django | Migration tests on both backends |
| `ingest` | Discover complete missions, call the core, turn `MissionResult` into level-1 rows (`persist`), update level-2 aggregates, archive logs, record runs. Also `reprocess` and `rebuild-aggregates`. **All aggregation happens here.** | core, db | Integration tests on SQLite and Postgres |
| `queries` | Thin, named read functions such as `get_sortie_detail(id)` and `get_player_profile(player)`. Simple SELECT/WHERE with simple joins only, no aggregation (TD-22). | db | Integration tests on SQLite and Postgres, plus query-count checks |
| `web` | URLs, views, templates, HTMX partials, admin. Thin. | queries, db | View smoke tests (status 200, key content) |
| `cli` | The `il2ks` entry point and config loading. | all | A few end-to-end tests |

The inner layers must not import `django` or `il2ks.db`. Enforce this mechanically, for example with an
`import-linter` contract in CI, so AI-written code can't quietly break the rule.

## Ingestion pipeline

```
take the single-writer lock (FR-ING-20)
reconcile: archive any ingested mission lacking a verified archive (FR-ING-8)
discover_missions(log_dir) ─► for each COMPLETE mission that is new, or whose part-file fingerprint changed (FR-ING-18),
                              skipping failed missions still in retry backoff (FR-ING-19):
    files  = ordered log files of the mission
    archive(files) and verify it                # FIRST: the archive is the source of truth
    events = logparse.parse_files(files)        # generator; bad lines → warnings, not exceptions
    result = replay.run(events, catalog)        # pure, deterministic
    with transaction.atomic():
        upsert(result)                          # level 1, in place by natural key, so URLs stay stable (FR-ING-9)
        update_aggregates(result)               # level 2: Player, PlayerAircraft (+ PlayerTour in it2); incremental, rebuildable (TD-08)
        record IngestRun(ok, fingerprint, archive path + checksum, counts, warnings)
    move originals out of the log folder        # default after verified archive (FR-ING-10)
on exception: record IngestRun(failed, traceback, next_retry_at); continue to next mission
```

- **Re-ingesting a mission** (late parts, retry, reprocess): before upserting, subtract the mission's *old* level-1 contribution from the
  level-2 totals of the affected players (or recompute just those players from level 1), so incremental totals never double-count.
  The aggregate-rebuild test (doc 08) covers it.
- **Mission key** `[PROPOSED]`: the log file name timestamp plus the server ID (see TD-17). Korea's logs
  don't include a mission ID (AType 0 `MID:` is empty).
- **Completeness detection**: see FR-ING-2. In remote log mode (FR-ING-16), only read parts whose size has stopped changing.
- **Backfill**: `il2ks reprocess` parses and replays archived missions in parallel worker processes (low priority). One writer process
  upserts the results (SQLite has one writer), then
  `rebuild-aggregates` once at the end (NFR-PERF-4).
- **New log content** (every couple of years): add an event dataclass and mapping in `logparse`, and a handler in `replay` if it
  matters. Unknown content is already kept and counted (TD-20).
- **Streaming**: `run()` is just `feed()` for every event followed by `finish()`. The same `Replay` object will serve "online now" (it2) and live sorties
  (later) through `snapshot()`, without a second code path (TD-07).
- **Determinism**: `replay.run` with the same input always gives the same output. That property
  makes golden tests and `reprocess` trustworthy.

## Process model `[PROPOSED]`

- `il2ks ingest`: one shot. Process everything that's ready, then exit. Suits Windows Task Scheduler or cron.
- `il2ks watch`: a loop that runs `ingest` every N seconds (default 30) until stopped.
- `il2ks web`: granian (TD-10) serving Django (WSGI) and static files.
- `il2ks run`: a small supervisor that starts `web`, `watch`, and the bundled Caddy (unless `https.mode = "external"`) as child
  processes and restarts them if they crash. **This is what installers and Docker call**, so admins have only one thing to run.
- Later (it2): live reading of the in-progress mission for "online now" (FR-ING-12). That's part of `watch`, using the same parser and replay.

## Read side and aggregates `[DECIDED]`

- The **raw log archive** (kept forever) is the source of truth. The DB holds **pre-aggregated tables shaped for the pages** (TD-08).
  Anything a page doesn't need gets dropped, and can be recomputed from the archive.
- Level 1 (per mission) is written at ingest. Level 2 (all-time, per aircraft, and per tour from it2) is updated incrementally and can always be rebuilt
  from level 1 (`il2ks rebuild-aggregates`). Nothing is ever hand-patched. That replaces the old system's pile of "retro compute"
  jobs and data-fix migrations (see [09_legacy_system_notes.md](09_legacy_system_notes.md)).
- **Views never aggregate** (TD-22). Counters live in columns. Simple arithmetic on them (ratios like K/D) happens at read time, and ratios aren't stored.
  More table levels may be added as pages need them. The page → table map is in [06_data_model.md](06_data_model.md).

## Front end `[DECIDED]`

- Django templates and a base layout. Pages work without JS. HTMX handles progressive
  enhancement: live player search, pagination and filtering without full reloads, and expanding the sortie timeline.
- **Pico CSS**, desktop-first and table-heavy. Mobile-friendly is a stretch goal (TD-05).
- No Node or npm build step. `htmx.min.js` and `pico.min.css` are vendored under `static/`.
- Light charts later (FR-WEB-16): server-rendered SVG or a small vendored chart library. Decide when needed.
- **Customization (TD-25):** branding from `SiteSettings` (admin) gets injected as CSS variables. `<data dir>/custom/templates` and
  `custom/static` come first in the template and static search paths. Templates are small, with named blocks and documented context,
  because overrides depend on them.
- **i18n (TD-24):** all strings are wrapped. English in v1, five more languages in it2.

## Proposed repo layout `[PROPOSED]`

```
il2_korea_stats/
├── pyproject.toml            # uv-managed; console script `il2ks`
├── uv.lock
├── CLAUDE.md                 # guidance for Claude Code; points at design_doc/
├── design_doc/
├── src/il2ks/
│   ├── core/
│   │   ├── logparse/         # events.py (dataclasses), parser.py, files.py
│   │   ├── replay/           # state.py, rules.py, result.py
│   │   └── catalog/          # data/*.csv|toml, loader.py
│   ├── db/                   # Django app: models.py, migrations/
│   ├── ingest/               # discover.py, persist.py, aggregates.py, archive.py, runner.py
│   ├── queries/              # missions.py, players.py, sorties.py
│   ├── web/                  # Django app: urls.py, views/, templates/, static/
│   ├── settings.py           # reads config file + env
│   └── cli.py                # `il2ks` entry point
├── tests/
│   ├── unit/                 # core: no DB
│   ├── integration/          # DB tests, run on SQLite and Postgres
│   └── fixtures/logs/        # anonymized real logs + synthetic scenarios
├── docker/                   # Dockerfile, compose.yaml
└── packaging/windows/        # installer scripts, bundled Caddy config
```
