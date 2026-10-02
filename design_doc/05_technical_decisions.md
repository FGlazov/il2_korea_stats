# 05 — Technical Decisions (decision log)

Each entry lists its status, the decision, the reasoning, and the alternatives considered. Numbers are stable and never reused.
Version numbers were checked against what was current as of 2026-10. Re-check them when starting implementation.

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
  template overriding (TD-25), and continuity with the old system.
- **Alternatives:** FastAPI + Jinja. It's lighter, but has no admin, no migrations story, and needs more glue. Rejected.

### TD-04 Database: SQLite for users; PostgreSQL kept working on the dev side only — `[DECIDED]` (2026-10-02)
- **Decision (maintainer, 2026-10-02):** **SQLite is the only database shipped to and supported for server admins**: in the Windows
  installer, the manual install, and Docker. **PostgreSQL is not a user-facing option.** It's maintained **on the dev side only**: dev
  environments and CI run the full test suite on both backends, and `il2ks db copy` exists, so a future switch (for example for the
  global-stats server) stays cheap and proven (TD-19). Admin docs and installer don't mention Postgres.
- **History:** The original plan was Postgres only. Claude raised SQLite to simplify installation. The maintainer's answer: "SQLite is also fine.
  Keep the dev environment on both, and write tests that prove switching later wouldn't be hard."
- **Versions:** PostgreSQL 17 or 18 via **psycopg 3**. SQLite ≥ 3.45 (whatever the bundled Python ships), in WAL mode.
- **No Postgres extensions** (the old system needed `hstore` and `citext`, which meant a manual SQL step). Portability rules are in TD-19.

### TD-05 Front end: server-side rendering with Django templates + HTMX + Pico CSS — `[DECIDED]` (2026-10-02)
- **Decision:** Django templates for every page. HTMX for partial updates (search-as-you-type, pagination,
  expandable sections). No JS build step. htmx is vendored (NFR-OFF-1).
- **CSS:** **Pico CSS** (minimal, classless-friendly, one file, styles tables well). It's a stats site: mostly tables of data.
  Branding colors map onto Pico's CSS variables (TD-25).
- **Charts:** only light diagrams, later (FR-WEB-16). Server-rendered SVG or a small vendored chart library. Decide when needed.
- **Mobile:** not required. Mobile-friendly layout is a stretch goal (Pico is responsive by default; wide tables scroll horizontally).
- **Alternatives considered:** React/Vue SPA (rejected: too complex for the maintainer and the goals), Bootstrap (heavier than needed),
  Alpine.js (allowed later for tiny client-side bits), Streamlit/Dash (rejected: not a public website framework).

### TD-06 Ingester: idempotent one-shot command plus a watch loop — `[PROPOSED]`
- **Decision:** `il2ks ingest` processes every complete mission that isn't in the DB yet, then exits. `il2ks watch` runs it in a loop.
- **Why:** The maintainer asked for a "separate scheduled job". A one-shot idempotent command works with *any*
  scheduler (Task Scheduler, cron, the Docker restart policy, the `run` supervisor) and is easy to test.
  The old system was a `while True` loop with state kept in memory.
- **Future live mode** (FR-ING-12 in it2, FR-ING-15 later): the replay is an event-stream state machine, so it can be fed
  incrementally from the in-progress mission. Don't build anything that assumes "the whole mission is in memory before processing starts".

### TD-07 A pure-Python core for parsing and replay — `[PROPOSED]`
- **Decision:** `core.logparse` and `core.replay` don't import Django. Events are frozen dataclasses (or `msgspec`
  structs if profiling shows a need). Replay gives back a plain `MissionResult`.
- **Why:** Fast, isolated tests. Rules are easy to reason about. The future global-stats system can reuse it. An
  `import-linter` contract enforces the boundary.
- **Streaming-ready interface** `[DECIDED]` (2026-10-02): replay is built as an incremental state machine from day one, not one big function:
  ```python
  r = Replay(catalog, rules)
  r.feed(event)      # update state with one event
  r.snapshot()       # provisional view: active sorties, running counts (for "online now" / live sorties)
  r.finish()         # resolve pending rules, return the final MissionResult
  run(events)        # batch mode = feed every event, then finish(); v1 only uses this
  ```
  - Rules that need **lookahead** (bailout: `PLID:0` end plus the pilot's final position; structural failure: did the wreck keep falling;
    "mission ended": AType 7) keep pending state and resolve when their trigger event arrives, or on `finish()`. `snapshot()` marks
    unresolved fields as provisional.
  - **No persisted replay state.** After a restart, the watcher re-reads the in-progress mission from its first file (a full mission is
    about 6 MB, which takes a second or two to parse).
  - **Only `finish()` writes final level-1 rows.** Provisional snapshots go to a small live table or stay in memory. Final stats are therefore
    computed the same way whether a mission was streamed or batch-processed, so golden tests and `reprocess` stay valid.
  - Test: feeding events one at a time and calling `finish()` must give exactly the same `MissionResult` as `run()`.

### TD-08 Archived logs are the source of truth; the DB holds pre-aggregated read models — `[DECIDED]` (2026-10-02)
- **Decision (maintainer):** "Pre-aggregate for whatever a view on the website requires, drop most of the rest. Store positions when you can."
  The DB isn't a normalized event store. It holds tables shaped for the pages: `Mission`, `Player`, `PlayerSortie`,
  `PlayerMission`, `PlayerAircraft`, and in it2 `Tour` and `PlayerTour` (see [06_data_model.md](06_data_model.md)).
- **Source of truth = the raw log archive**, kept forever (TD-09). Anything dropped can be recomputed by `il2ks reprocess` (backfill)
  when stats logic changes.
- **Levels of tables** (at least two to start; more get added if pages need them, under the same rule: every level rebuilds from the one below):
  1. **Mission-level** (written when one mission is ingested, depends only on that mission): `Mission`, `PlayerSortie` (with its
     timeline, damage breakdown, ammo, and key-event positions), `Kill`, `PlayerMission`. Can be rebuilt per mission, in parallel.
  2. **Cross-mission** (`PlayerAircraft`, all-time totals on `Player`, and in it2 `PlayerTour`): updated **incrementally** at ingest by adding the
     new mission's level-1 rows, and always **rebuildable from level 1** (`il2ks rebuild-aggregates`: plain sums, maxes, and ordered passes for
     streaks). Incremental updates are safe *because* a rebuild exists. That's exactly what the old system lacked (its `fix_*` jobs and migrations).
- **Positions:** stored on the key events we keep (spawn, takeoff, landing, kills, death, bailout, sortie end). There's **no flight track**:
  the logs have no periodic position updates (AType 17 never appears), so positions during cruise are unknown
  ([12](12_korea_log_format.md#position-data)). That's enough for a future map of key events (FR-WEB-12).
- **Future position source** `[PROPOSED]` (2026-10-02): continuous positions may come from a **different source** later, for example a
  Tacview-style live telemetry feed if the game offers one (not verified for Korea, OQ-26). Design rule: it's a **separate input with separate
  storage**, not part of the log pipeline.
  - A separate collector (an `il2ks track` process supervised by `run`) connects or polls during the mission and writes downsampled points to
    their own table (`PositionSample`: mission, object or sortie, time, x/y/z, maybe speed and heading), or to compressed per-mission track files.
  - It joins to sorties by mission time and object ID mapping. The mapping between telemetry IDs and log IDs is the main risk, so verify it first.
  - `core.replay` and the stats don't depend on it. If the feed is missing or broken, stats stay correct and only the map loses detail.
  - Volume is fine downsampled (for example one point per 5 s: ~73 sorties × ~20 min ≈ 17k rows per mission).
- **Dropped:** `AMMO:explosion` hits (97% of AType 1) are never **stored**, but replay still uses them in memory to attribute bomb and rocket
  damage (FR-WEB-18). Other hit and damage lines become per-sortie and per-pair aggregates.

### TD-09 Archive raw logs forever — `[DECIDED]` (2026-10-02)
- **Decision:** Compressed archive per mission (zip or zstd), **kept forever by default** (retention stays configurable). Logs
  compress roughly 15×, so a busy server's year is about 1–2 GB. `il2ks reprocess` rebuilds from the archive (backfill after stats changes).

### TD-10 Production web server: granian — `[DECIDED]` (2026-10-02)
- **Decision (maintainer):** **granian** (Rust-based, cross-platform including Windows, ships as a Python wheel so uv installs it)
  serves Django through its WSGI interface. **WhiteNoise** serves static files from the same process. It listens on **localhost only**,
  behind the HTTPS proxy (TD-23).
- **Alternative considered:** waitress (pure Python, used by the old system). It's slower, and was kept only as a fallback idea.
- **Not gunicorn**, because it doesn't run on Windows.

### TD-11 Configuration — `[PROPOSED]`
- **Decision:** One `il2ks.toml` (commented, created by `il2ks setup`), with environment variable overrides (`IL2KS_*`)
  for Docker. Secrets (Django secret key, DB password) are generated at setup.
- Settings are loaded once at startup and passed in explicitly. Modules don't read globals at import time (the old system
  read `settings.X` at import time).

### TD-12 Quality tooling — `[DECIDED]` (ruff, mandatory tight typing), `[PROPOSED]` (type checker setup)
- pytest, pytest-django, and coverage. **ruff** for lint and format `[DECIDED]` (2026-10-02). import-linter. pre-commit. GitHub Actions CI on Windows and Linux.
- **Type hints are mandatory and kept as tight as possible** `[DECIDED]` (maintainer, 2026-10-02):
  - ruff `ANN` rules fail on any missing parameter or return annotation, and `ANN401` bans `Any` in annotations.
  - Prefer precise types: frozen dataclasses for events and results, `Literal`/`Enum` for closed value sets (`pilot_fate`, `loss_cause`),
    `NewType` for IDs (`AircraftId`, `PilotId`, `AccountUuid`) so they can't be mixed up, and no bare `dict`s across layer boundaries.
- **Type checker: pyright in `strict` mode** for the whole project `[PROPOSED]`. It's the same engine as Pylance in VS Code, so editor and CI
  agree, and it's fast enough for the Claude Code hooks.
  - `core/` (parser, replay, catalog): strict with **zero** ignores.
  - Django layers (`db`, `queries`, `web`, `ingest`): strict, with `django-types` stubs. Only targeted, commented `# pyright: ignore[rule]`
    where the dynamic ORM can't be typed. Unnecessary ignores are errors (`reportUnnecessaryTypeIgnoreComment`).
  - Alternative considered: mypy `--strict` + `django-stubs` (deeper Django plugin, but slower and doesn't match the editor). Astral's `ty`:
    watch it, but don't adopt it yet.
- **Data checks without extra frameworks** `[DECIDED]` (2026-10-02): invariants are **database constraints** (`CheckConstraint` /
  `UniqueConstraint`, for example `kills >= 0`, `0 <= damage_taken <= 1`, allowed `pilot_fate` values, one `PlayerMission` per player and mission),
  so they hold in production too. Logic and query tests are plain pytest on fixture missions. **No dbt** (the core logic is stateful Python, and
  it would be a second toolchain for admins; maybe reconsider for the iteration-3 warehouse) and **no pandera** for now.
- Details in [08_development_workflow.md](08_development_workflow.md).

### TD-13 No cloud dependency — `[DECIDED]`
- Everything runs on the server machine. No external calls at runtime.
- **The one exception:** certificate issuance and renewal for HTTPS (ACME, TD-23). It's a certificate authority, and none of our data leaves the machine.

### TD-14 Packaging and distribution: Windows-native first — `[DECIDED]` (target), `[PROPOSED]` (mechanism)
- Target hosts run **Windows**, and admins **have admin rights**, so installing Windows services and opening firewall ports are allowed
  (maintainer, 2026-10-02; Discord confirmation is a formality).
- One install per game server (FR-OPS-5). Details and options in [07_deployment_and_installation.md](07_deployment_and_installation.md).

### TD-15 Time handling — `[PROPOSED]`
- Store every timestamp in UTC (`USE_TZ=True`). Log file names contain the server's *local* time, so take the
  server timezone from config, defaulting to the OS timezone. Game-world date and time (the in-mission date) is a separate field.
- Tick-based timings inside a mission get converted in `core.replay`. **Verified for Korea: 50 ticks = 1 s.**
- **Display in the viewer's local time** (FR-WEB-17, a stretch goal, not in the PoC; v1 shows times in UTC, labelled as UTC). `[PROPOSED]` mechanism for later: templates render every real-world timestamp as
  `<time datetime="2026-09-19T20:34:13Z">2026-09-19 20:34 UTC</time>`, and a few lines of vendored JS convert all `<time>` elements to the
  browser's timezone with `Intl.DateTimeFormat` (also after HTMX swaps). Why this way: no cookie, no account, nothing for the server to know,
  and pages stay identical for every viewer (cache-friendly). Without JS, the page still shows correct UTC times. Optional extras: a "UTC/local"
  toggle in the footer, and relative times ("2 h ago") on lists.
- **Not converted:** game-world date and time (the mission's in-game clock, which is part of the scenario), and durations or flight times.

### TD-16 Extensibility through explicit extension points, never monkeypatching — `[PROPOSED]`
- **Context:** The maintainer's own `mod_rating_by_type` and `mod_stats_by_aircraft` for `il2_stats` added
  valuable features, but they did it by **monkeypatching** core views, URLs, report methods, loader functions,
  and model methods at `AppConfig.ready()`. That breaks silently whenever the core changes. See
  [09_legacy_system_notes.md](09_legacy_system_notes.md).
- **Decision:** Optional features are first-class and toggled in config. Each one plugs in through a small number
  of defined seams:
  - **Replay rules**: rules are separate functions or strategy objects that `replay` calls (for example kill-credit policy,
    the suspected early bailout heuristic, "parachute deaths"). They're configured, not patched.
  - **Aggregators**: a registry of functions that build level-2 tables from level-1 tables (TD-08). Adding
    one (aircraft stats, split rankings by aircraft class, gunner stats) doesn't touch the others.
  - **Web**: features add their own URLs, views, and template blocks or nav entries, through a registry or template
    `{% block %}`s. They never replace core views.
- Don't build a general plugin system until a second feature actually needs it. Keep the seams simple.

### TD-17 One install per server, but ready for multi-server later — `[DECIDED]` (one install per server), `[PROPOSED]` (IDs)
- Each install serves exactly one game server (maintainer, 2026-10-02). Running several DServers on one machine is the exception, and gets handled by
  several installs side by side (FR-OPS-5).
- Every mission row still carries a `server_uid` (a UUID generated at setup), and player identity uses the game's account UUIDs. That's
  enough to merge data from several servers in a future global system (iteration 3) without migrating the core schema.

### TD-18 Credit and reuse of `il2_stats` — `[DECIDED]` (credit and MIT attribution), `[PROPOSED]` (reuse)
- **MIT attribution is in place** (2026-10-02): the root `NOTICE` file credits =FB=Vaal and =FB=Isay (the "IL2 stats team",
  https://github.com/vaal-/il2_stats) and reproduces their copyright line ("Copyright (c) 2015 IL2 stats team") and MIT permission notice.
  This satisfies the MIT condition for any portions derived from `il2_stats`. Keep `NOTICE` in every distribution (installer, Docker image, sdist/wheel).
- The project README (when written) also states the project is **inspired by `il2_stats`**. The maintainer will talk to the authors directly.
- Any regex patterns or replay rules we port get a short source comment (`# Derived from il2_stats (MIT), see NOTICE`). Port them into
  the new structure. Don't copy whole modules.

### TD-19 Database portability rules — `[PROPOSED]`
To keep "switch SQLite ↔ Postgres" cheap and *proven*:
- **Allowed:** standard ORM fields, `JSONField` (works on both), `db_index`, `UniqueConstraint`, `Q`/`F` expressions, simple joins.
  Aggregation functions are allowed only in **ingest and rebuild code**, never in views (TD-22).
- **Not allowed:** `ArrayField`, `HStoreField`, `CIText*`, `DISTINCT ON` (`.distinct(*fields)`), `SearchVector` and other
  `django.contrib.postgres` features, raw SQL. A test or import-linter rule fails CI if `django.contrib.postgres` is imported anywhere.
- **Case-insensitive search:** store a normalized `name_lower` column with an index.
- **Timezones:** store UTC and let Django handle conversion. Don't rely on DB timezone functions.
- **Tests:**
  1. **SQLite is the default test database** everywhere: plain `uv run pytest`, the Claude Code hooks, pre-commit, and the main CI jobs
     (maintainer, 2026-10-02). Postgres is **opt-in** with `IL2KS_TEST_DB=postgres`, and runs in one separate CI job. Postgres-only tests
     (like the transfer test below) skip automatically when no Postgres is configured.
  2. A **migration round-trip test**: apply all migrations from zero on both backends, then `makemigrations --check`
     (no missing migrations).
  3. A **data transfer test**: ingest the fixture missions into SQLite, copy everything to Postgres with the
     copy tool (below), and assert identical page data (row counts per table plus key query outputs such as player totals
     and sortie details).
- **Tool:** `il2ks db copy --from <url> --to <url>` copies all tables in dependency order, in batches, then resets
  sequences on Postgres. That one command is the whole migration path, and the transfer test above covers it.

### TD-20 Parsing robustness: the log format will change, the parser must not crash — `[DECIDED]` (2026-10-02)
- **Context (maintainer):** new information shows up in the logs only occasionally, maybe every couple of years with a game update, but it
  *will* happen. Adding support for a new event type or field must be a **local change**: one event dataclass, its key mapping,
  an optional replay handler, and tests. Nothing else should need touching.
- Parse `KEY:value` tokens **generically**, then map them to typed events, instead of one strict regex per type. Unknown trailing
  keys (like `MID:` on AType 12 and `TARGETS()` on AType 8) get kept in an `extra` dict, not rejected.
- Event types we don't use yet (AType 27, 28) and ones never seen (22, 23, 29) parse into a generic event, get counted, and are
  otherwise ignored (maintainer: "ignore them for now"). The same goes for any future unknown AType.
- Values that can contain spaces or commas (object `TYPE`, player `NAME`, `SKIN`) are parsed by anchoring on the
  *next known key*, never with `[^,]` style patterns.
- Unknown object types get auto-registered (FR-ING-7). The replay never indexes a catalog dict directly.
- Re-declaring a known object ID (AType 12) updates it. It doesn't replace it.
- Format drift shows up in `IngestRun` (log `VER`, counts of unknown keys and types), so admins and maintainers notice.

### TD-21 Game rules baseline: port the `il2_stats` rules — `[DECIDED]` (2026-10-02)
- Kill credit, assists, sortie outcomes, capture on enemy territory, and so on start as a port of the `il2_stats` `MissionReport`
  logic (see [09_legacy_system_notes.md](09_legacy_system_notes.md)). Korea-specific adaptations: pilot fate without AType 18,
  the suspected early bailout heuristic (FR-ING-14), AType 12 re-declaration, and 2D area polygons.
- Every ported rule gets a scenario test that documents it (an executable rulebook).

### TD-22 Views only do simple reads — `[DECIDED]` (2026-10-02)
- **Decision (maintainer):** At request time, Django only runs `SELECT … FROM … WHERE … ORDER BY … LIMIT` queries, with at most
  simple joins (FK lookups, `select_related`). No `GROUP BY`, no aggregation, no subqueries, no window functions in views. Counters
  live in pre-aggregated tables (TD-08). Paginator `COUNT(*)` is allowed.
- **Simple arithmetic on columns at read time is fine, and preferred over storing derived values.** Ratios like K/D (`kills / deaths`),
  K/L, kills per hour and survival rate are computed from the stored counters, as a model property, a template filter, or an `F()`
  expression. **Don't store ratios as columns** (maintainer, 2026-10-02). What's ruled out is business logic at serving time: rules,
  classification, multi-step computations. Those belong in ingest.
- **Why:** pages stay fast on SQLite, queries are trivially portable, and the code is easy to vibe-code and review.
- **Enforcement:** every view gets a test with `django_assert_max_num_queries`, plus a test that inspects captured SQL for
  `GROUP BY` and aggregate functions in view tests. The `queries/` layer stays thin.

### TD-23 HTTPS only, through bundled Caddy or the admin's own proxy — `[DECIDED]` (2026-10-02)
- **Decision (maintainer):** The site is served over HTTPS only. Plain HTTP only redirects. Django runs with `SECURE_SSL_REDIRECT`,
  HSTS, secure cookies, and `SECURE_PROXY_SSL_HEADER`.
- **Mechanism (the maintainer deferred to Claude's judgment):** bundle **Caddy** (a single static exe, Windows-native, automatic certificates via ACME, automatic
  HTTP→HTTPS redirect) as the reverse proxy in front of the WSGI server. `il2ks run` supervises it. The WSGI server listens on localhost only.
- **Why Caddy:** it's the least work for a non-expert. One binary, a few config lines, and it obtains and renews certificates by itself, with no certbot
  or scheduled task. Downsides: one more process to supervise, and it needs ports 80 and 443 (also true for any other HTTPS setup).
- **Certificate fallback order** (`il2ks setup` asks for a domain and picks the first that works):
  1. **Domain name** pointing at the server: a normal publicly trusted certificate. This is the recommended setup. Free dynamic-DNS names work.
  2. **No domain, public IP:** a short-lived Let's Encrypt certificate for the IP address (verify Caddy and ACME support at implementation time).
  3. **Last resort:** Caddy's internal CA (self-signed), with browser warnings. Documented as "for testing only".
- **Bring your own proxy (supported):** admins who already run **nginx or IIS** set `https.mode = "external"`. The bundled Caddy stays off, and
  the docs ship sample nginx and IIS (URL Rewrite / ARR) configs that forward to `127.0.0.1:8000` with the right `X-Forwarded-Proto` header.
- **Several installs on one machine** (FR-OPS-5) can't all own port 443. Document using different ports, or one shared Caddy.

### TD-24 Internationalization — `[DECIDED]` (2026-10-02)
- **v1:** English only, but every UI string is wrapped for translation from day one (`{% translate %}`, `gettext_lazy`), so adding
  languages later doesn't mean touching every template.
- **Iteration 2:** Russian, German, Spanish, French, and Brazilian Portuguese. First drafts get machine-translated by an LLM, then reviewed by
  human translators. Korean isn't planned.
- **Game object names** (aircraft, vehicles, ships, …) `[DECIDED]` (2026-10-02), **iteration 2**. Admin overrides are required for the public
  release; translations aren't a release gate (v1 shows the English default names from the shipped catalog): **we set the defaults, including translations**. The catalog
  data shipped with the package (`core/catalog/data/`) holds each object's display name in English, plus the it2 languages as they're added.
  Admins **may** edit names in the admin. Their edits are stored as overrides on top of the shipped defaults, so upgrades refresh the defaults
  without wiping admin edits, and an override can be reset to the default. Names fall back from the viewer's language to English to the raw log name.

### TD-25 Customization: branding in the admin, plus a `custom/` override folder — `[DECIDED]` (2026-10-02)
- **Layer 1, no files touched:** `SiteSettings` in the admin holds the title, server name, logo upload, accent colors (mapped to Pico CSS
  variables), description, and links. Most owners only need this.
- **Layer 2, `custom/` overrides** ("very important for some server owners"): `custom/templates/` and `custom/static/` live in the
  **data directory** (so upgrades never overwrite them) and come first in `TEMPLATES['DIRS']` / `STATICFILES_DIRS`. Any built-in template
  or static file can be overridden by putting a file with the same path there.
- **Consequence:** template names, `{% block %}`s, and context variables become a **semi-public API**. Keep templates small, with named
  blocks, and document their context. Mention breaking template changes in release notes. `il2ks doctor` warns when an overridden
  template's original has changed since the override was made (hash comparison).

### TD-26 Tours with configurable length, in iteration 2 — `[DECIDED]` (tours, it2), `[PROPOSED]` (modes)
- Missions belong to exactly one `Tour`, assigned by mission start time. Per-tour totals (`PlayerTour`) are the main stats unit, with all-time
  totals alongside.
- Config `tours.mode`: `"monthly"` (calendar month, **default**), `"days:<N>"` (a rolling period of N days from a configurable start date), or `"manual"`
  (the admin starts a new tour, FR-ADM-8).
- Changing the mode later means reassigning missions to tours and rebuilding the level-2 aggregates (cheap, TD-08).
- **Scheduled for it2** (maintainer, 2026-10-02). v1 shows all-time stats only. Adding tours later is a new level-2 table plus a rebuild,
  so v1 needs no special preparation beyond keeping `started_at` on `Mission`.

### TD-27 Observability: log files now, self-hosted monitoring later — `[DECIDED]` (2026-10-02)
- **v1:** each process (`web`, `watch`, Caddy) writes **rotating log files** in the data directory, plus stdout. The level is configurable.
  Ingestion history is visible in the admin (`IngestRun`). `il2ks doctor` checks the processes are healthy. Nothing else.
- **Later:** optional metrics and error tracking. Datadog works but is a paid cloud service. Self-hostable open-source alternatives the
  maintainer could run: **Grafana + Loki + Prometheus** (logs + metrics), **SigNoz** or **OpenObserve** (all-in-one, OpenTelemetry-based),
  and **GlitchTip** (Sentry-compatible error tracking). The cheapest path is structured (JSON) logs from day one, plus OpenTelemetry later,
  so any of these can be plugged in. Opt-in only, because it sends data off the machine (TD-13).
