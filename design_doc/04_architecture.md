# 04 — High-Level Architecture

## System context

```
 ┌──────────────────────┐   writes    ┌───────────────────────────┐
 │ IL-2 Korea DServer   │ ──────────► │ log directory (text logs) │
 └──────────────────────┘             └─────────────┬─────────────┘
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
                                      │ SQLite or PostgreSQL      │
                                      └─────────────┬─────────────┘
                                                    │ read (mostly)
                                                    ▼
 ┌──────────────┐    HTTP (HTML,     ┌───────────────────────────┐
 │ Browser      │ ◄────────────────► │ WEB (Django + HTMX)       │
 │ (players)    │   HTMX fragments)  │ WSGI server, static files │
 └──────────────┘                    └───────────────────────────┘
```

There are two OS processes and one database `[DECIDED]`. The ingester and the web app share the Django
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
| `core.replay` | A state machine that consumes an event stream and produces an immutable `MissionResult` (mission metadata, sorties, kills, damage, timeline). **All game rules live here**: kill credit, sortie outcome, bailout or capture, and so on. | `logparse`, `catalog` | Scenario tests (small synthetic logs) plus golden snapshot tests (real logs → expected JSON) |
| `core.catalog` | Static reference data: object names → class (fighter, bomber, AAA…), countries → coalitions. Default data ships in the package, and admin overrides live in the DB. | stdlib | Unit tests |
| `db` | Django models (normalized facts plus rebuildable aggregates), migrations. | Django | Migration tests |
| `ingest` | Discover complete missions, call the core, map `MissionResult` → ORM rows (`persist`), archive logs, record runs. | core, db | Integration tests on SQLite and Postgres |
| `queries` | Named read functions such as `get_sortie_detail(id)` and `player_totals(profile_id)`. Views don't build ad-hoc ORM chains. | db | Integration tests on SQLite and Postgres |
| `web` | URLs, views, templates, HTMX partials, admin. Thin. | queries, db | View smoke tests (status 200, key content) |
| `cli` | The `il2ks` entry point and config loading. | all | A few end-to-end tests |

The inner layers must not import `django` or `il2ks.db`. Enforce this mechanically, for example with an
`import-linter` contract in CI, so AI-written code can't quietly break the rule.

## Ingestion pipeline

```
discover_missions(log_dir) ─► for each COMPLETE mission not yet in DB (by mission key):
    files  = ordered log files of the mission
    events = logparse.parse_files(files)        # generator; bad lines → warnings, not exceptions
    result = replay.run(events, catalog)        # pure, deterministic
    with transaction.atomic():
        persist(result)                         # facts
        update_aggregates(affected players)     # or rebuild; see TD-08
        record IngestRun(ok, counts, warnings)
    archive(files)                              # compress to archive dir; optional delete
on exception: record IngestRun(failed, traceback); continue to next mission
```

- **Mission key** `[PROPOSED]`: the log file name timestamp plus the server ID (see TD-17). Korea's logs
  don't include a mission ID (AType 0 `MID:` is empty).
- **Completeness detection**: see FR-ING-2. This depends on the log format.
- **Determinism**: `replay.run` with the same input always gives the same output. That property
  makes golden tests and `reprocess` trustworthy.

## Process model `[PROPOSED]`

- `il2ks ingest`: one shot. Process everything that's ready, then exit. Suits Windows Task Scheduler or cron.
- `il2ks watch`: a loop that runs `ingest` every N seconds (default 30) until stopped.
- `il2ks web`: the production WSGI server (see TD-10) serving Django and static files.
- `il2ks run`: a small supervisor that starts `web` and `watch` as child processes and restarts
  them if they crash. **This is what installers and Docker call**, so admins have only one thing to run.

## Read side and aggregates

- Facts (missions, sorties, kills, damage) are the **source of truth**.
- Totals per player (and later per aircraft or per tour) live in summary tables that are
  **derived and rebuildable** (`il2ks rebuild-aggregates`). They're never hand-patched.
  If the logic changes, rebuild them. That replaces the old system's pile of "retro compute"
  jobs and data-fix migrations. See [09_legacy_system_notes.md](09_legacy_system_notes.md).
- For v1, compute small aggregates on the fly with SQL and add summary tables only when a page
  measurably needs one.

## Front end `[PROPOSED]`

- Django templates and a base layout. Pages work without JS. HTMX handles progressive
  enhancement: live player search, pagination and filtering without full reloads, and expanding the sortie timeline.
- No Node or npm build step. `htmx.min.js` and one CSS file are vendored under `static/`.
- Charts (later) could use server-rendered SVG or a small vendored chart library. Decide when needed.

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
│   ├── ingest/               # discover.py, persist.py, archive.py, runner.py
│   ├── queries/              # missions.py, players.py, sorties.py
│   ├── web/                  # Django app: urls.py, views/, templates/, static/
│   ├── settings.py           # reads config file + env
│   └── cli.py                # `il2ks` entry point
├── tests/
│   ├── unit/                 # core: no DB
│   ├── integration/          # DB tests, run on SQLite and Postgres
│   └── fixtures/logs/        # anonymized real logs + synthetic scenarios
├── docker/                   # Dockerfile, compose.yaml
└── packaging/windows/        # installer scripts (if chosen)
```
