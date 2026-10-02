# 05 — Technical Decisions (decision log)

Each entry lists its status, the decision, the reasoning, and the alternatives considered. Entries are numbered in the order they were made. TD-19..21
(added 2026-10-02, after the sample logs arrived) come right after TD-04. Version numbers
were checked against what was current as of 2026-10. Re-check them when starting implementation.

---

### TD-01 Language: Python, modern version — `[DECIDED]` (Python) / `[PROPOSED]` (version)
- **Decision:** Python **3.13 or 3.14** (both have years of security support left). Pin it in `.python-version`;
  uv installs it automatically.
- **Why:** The maintainer works in Python, and Django is Python. The old system was stuck on 3.5 (P3).
- **Policy:** Support the two newest CPython minor versions. Bump them about once a year.

### TD-02 Dev environment and dependencies: uv — `[DECIDED]`
- **Decision:** `pyproject.toml` plus a committed `uv.lock`. Common commands: `uv sync`, `uv run pytest`, `uv run il2ks ...`.
- **Bonus:** uv can also download Python itself, which the Windows installer could use (see TD-14).

### TD-03 Web framework: Django — `[DECIDED]` (Django) / `[PROPOSED]` (version)
- **Decision:** Start on **Django 5.2 LTS** (supported until April 2028). Move to the next LTS (6.2, expected around April 2027) once it's out.
- **Why Django:** The ORM and migrations, a free admin for the server owner (FR-ADM), mature templates and i18n,
  and continuity with the old system.
- **Alternatives:** FastAPI + Jinja. It's lighter, but has no admin, no migrations story, and needs more glue. Rejected.

### TD-04 Database: SQLite **and** PostgreSQL, both first-class — `[DECIDED]` (2026-10-02)
- **Decision:** The app supports both backends through the Django ORM. Dev environments and CI run against **both**.
  Which one ships as the default for end users is decided together with packaging (TD-14). Leaning toward SQLite for the single-server
  Windows install, and Postgres for Docker and the future global-stats server.
- **History:** The original plan was Postgres only. Claude raised SQLite to simplify installation. The maintainer's answer: "SQLite is also fine.
  Keep the dev environment on both, and write tests that prove switching later wouldn't be hard."
- **Versions:** PostgreSQL 17 or 18 via **psycopg 3**. SQLite ≥ 3.45 (whatever the bundled Python ships), in WAL mode.
- **No Postgres extensions** (the old system needed `hstore` and `citext`, which meant a manual SQL step). Portability rules are in TD-19.

### TD-19 Database portability rules — `[PROPOSED]`
To keep "switch SQLite ↔ Postgres" cheap and *proven*:
- **Allowed:** standard ORM fields, `JSONField` (works on both), `db_index`, `UniqueConstraint`, `Q`/`F`/aggregations,
  window functions (both support them), `Lower()` for case-insensitive search.
- **Not allowed in shared code:** `ArrayField`, `HStoreField`, `CIText*`, `DISTINCT ON` (`.distinct(*fields)`),
  `SearchVector` and other `django.contrib.postgres` features, raw SQL. If something truly needs backend-specific SQL,
  it goes in `queries/` behind a function with an implementation and a test for **each** backend.
- **Timezones:** store UTC and let Django handle conversion. Don't rely on DB timezone functions.
- **Tests:**
  1. The whole test suite runs on both backends (pytest parametrized by `IL2KS_TEST_DB=sqlite|postgres`, CI matrix).
  2. A **migration round-trip test**: apply all migrations from zero on both backends, then `makemigrations --check`
     (no missing migrations).
  3. A **data transfer test**: ingest the fixture missions into SQLite, copy everything to Postgres with the
     copy tool (below), and assert identical query results (row counts per table plus key query outputs such as player totals
     and sortie details).
- **Tool:** `il2ks db copy --from <url> --to <url>` copies all tables in dependency order, in batches, then resets
  sequences on Postgres. That one command is the whole migration path, and the transfer test above covers it.
- A lint check (a simple grep test or import-linter rule) fails CI if `django.contrib.postgres` is imported outside `queries/`.

### TD-20 Parsing robustness: the game changes, the parser must not crash — `[PROPOSED]`
Lessons from running legacy code on Korea logs ([12_korea_log_format.md](12_korea_log_format.md)):
- Parse `KEY:value` tokens **generically**, then map them to typed events, instead of one strict regex per type. Unknown trailing
  keys (like the new `MID:` and `TARGETS()`) get kept in an `extra` dict, not rejected.
- Unknown ATypes become `UnknownEvent(atype, raw)`, which gets counted and kept. They never crash anything.
- Values that can contain spaces or commas (object `TYPE`, player `NAME`, `SKIN`) are parsed by anchoring on the
  *next known key*, never with `[^,]` style patterns.
- Unknown object types get auto-registered (FR-ING-7). The replay never indexes a catalog dict directly.
- Re-declaring a known object ID (AType 12) updates it. It doesn't replace it.
- Any game version change shows up in `IngestRun` (log `VER`, counts of unknown keys and types), so admins and
  maintainers notice format drift.

### TD-21 Game rules baseline: port the `il2_stats` rules — `[DECIDED]` (2026-10-02)
- Kill credit, assists, sortie outcomes, capture on enemy territory, and so on start as a port of the `il2_stats` `MissionReport`
  logic (see [09_legacy_system_notes.md](09_legacy_system_notes.md)). Korea-specific adaptations: inferred bailout (no AType 18
  for player pilots), AType 12 re-declaration, and 2D area polygons.
- Every ported rule gets a scenario test that documents it (an executable rulebook).

### TD-05 Front end: server-side rendering with Django templates + HTMX — `[DECIDED]` (SSR) / `[PROPOSED]` (HTMX)
- **Decision:** Django templates for every page. HTMX for partial updates (search-as-you-type, pagination,
  expandable sections). No JS build step. htmx is vendored (NFR-OFF-1).
- **CSS:** `[OPEN]` OQ-13. Options: a classless or minimal framework such as **Pico CSS** (looks decent with zero
  effort, and it's one file), or Bootstrap (heavier, but familiar). Leaning toward Pico or plain CSS.
- **Alternatives considered:** React/Vue SPA (rejected: too complex for the maintainer and the goals),
  Alpine.js (allowed later for tiny client-side bits), Streamlit/Dash (rejected: not a public website framework).

### TD-06 Ingester: idempotent one-shot command plus a watch loop — `[PROPOSED]`
- **Decision:** `il2ks ingest` processes every complete mission that isn't in the DB yet, then exits. `il2ks watch` runs it in a loop.
- **Why:** The maintainer asked for a "separate scheduled job". A one-shot idempotent command works with *any*
  scheduler (Task Scheduler, cron, the Docker restart policy, the `run` supervisor) and is easy to test.
  The old system was a `while True` loop with state kept in memory.

### TD-07 A pure-Python core for parsing and replay — `[PROPOSED]`
- **Decision:** `core.logparse` and `core.replay` don't import Django. Events are frozen dataclasses (or `msgspec`
  structs if profiling shows a need). Replay gives back a plain `MissionResult`.
- **Why:** Fast, isolated tests. Rules are easy to reason about. The future global-stats system can reuse it. An
  `import-linter` contract enforces the boundary.

### TD-08 Facts are the source of truth and aggregates can be rebuilt — `[PROPOSED]`
- **Decision:** Store normalized facts. Aggregates are derived (computed on query, or kept in summary tables
  that a command can rebuild). Never maintain counters incrementally if they can't be recomputed.
- **Event granularity** `[OPEN]` OQ-14: do we store every hit and damage event (large tables, enables future
  features like the sortie map and ammo breakdown), or only per-sortie summaries plus kills? Middle ground:
  store kills and damage per (attacker, target, sortie) pair, plus the per-sortie timeline. Keep raw logs in the archive
  so finer detail can be extracted later.

### TD-09 Archive raw logs so we can reprocess — `[PROPOSED]`
- **Decision:** Compressed archive per mission (zip or zstd), with configurable retention (default: keep forever,
  since logs compress well). `il2ks reprocess` rebuilds from the archive.

### TD-10 Production web server — `[PROPOSED]`
- **Decision:** **waitress** (pure Python, works on Windows, used by the old system) or **granian** (Rust, cross-platform,
  faster). Pick one during the PoC. Use **WhiteNoise** to serve static files from the same process.
- **Not gunicorn**, because it doesn't run on Windows.

### TD-11 Configuration — `[PROPOSED]`
- **Decision:** One `il2ks.toml` (commented, created by `il2ks setup`), with environment variable overrides (`IL2KS_*`)
  for Docker. Secrets (Django secret key, DB password) are generated at setup.
- Settings are loaded once at startup and passed in explicitly. Modules don't read globals at import time (the old system
  read `settings.X` at import time).

### TD-12 Quality tooling — `[PROPOSED]`
- pytest, pytest-django, and coverage. ruff (lint + format). pyright or mypy (pick one, probably pyright in basic mode
  to start). import-linter. pre-commit. GitHub Actions CI on Windows and Linux.
- Details in [08_development_workflow.md](08_development_workflow.md).

### TD-13 No cloud dependency — `[DECIDED]`
- Everything runs on the server machine. No external calls at runtime.

### TD-14 Packaging and distribution — `[OPEN]` (leaning toward Windows-native first)
- Research (2026-10-02): DServer is a Windows binary, so Windows is the primary target. See
  [07_deployment_and_installation.md](07_deployment_and_installation.md). We're still waiting on the operators' answers on Discord (OQ-2).

### TD-15 Time handling — `[PROPOSED]`
- Store every timestamp in UTC (`USE_TZ=True`). Log file names contain the server's *local* time, so take the
  server timezone from config, defaulting to the OS timezone. Game-world date and time (the in-mission date) is a separate field.
- Tick-based timings inside a mission get converted in `core.replay`. **Verified for Korea: 50 ticks = 1 s.**

### TD-16 Extensibility through explicit extension points, never monkeypatching — `[PROPOSED]`
- **Context:** The maintainer's own `mod_rating_by_type` and `mod_stats_by_aircraft` for `il2_stats` added
  valuable features, but they did it by **monkeypatching** core views, URLs, report methods, loader functions,
  and model methods at `AppConfig.ready()`. That breaks silently whenever the core changes. See
  [09_legacy_system_notes.md](09_legacy_system_notes.md).
- **Decision:** Optional features are first-class and toggled in config. Each one plugs in through a small number
  of defined seams:
  - **Replay rules**: rules are separate functions or strategy objects that `replay` calls (for example kill-credit policy,
    "bailout without damage counts as death", "parachute deaths"). They're configured, not patched.
  - **Derived stats / aggregators**: a registry of aggregator functions that turn facts into summary tables. Adding
    one (aircraft stats, split rankings by aircraft class) doesn't touch the others.
  - **Web**: features add their own URLs, views, and template blocks or nav entries, through a registry or template
    `{% block %}`s. They never replace core views.
- Don't build a general plugin system until a second feature actually needs it. Keep the seams simple.

### TD-17 Design for multi-server early, cheaply — `[PROPOSED]`
- Every mission row carries a `server_id` (a UUID generated at setup) and a stable `mission_uid`. Player identity
  uses the game's UUIDs, never local auto-increment IDs. That's enough to merge data from several servers later
  (iteration 3) without migrating the core schema. Nothing else for global stats gets built now.

### TD-18 Reusing `il2_stats` code — `[PROPOSED]`
- `il2_stats` is MIT-licensed, so we can port its regex patterns and replay rules as a *starting point*, with
  attribution in `NOTICE`/README. Port them into the new structure. Don't copy whole modules.
