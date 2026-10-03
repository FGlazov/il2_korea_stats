# 16 — Web and Operations (as built)

How the website, the admin and the operations commands are built (iteration 1, part 2, 2026-10-03). Requirements are in
[02](02_functional_requirements.md) (FR-WEB, FR-ADM, FR-OPS), decisions in [05](05_technical_decisions.md). Everything here is
`[PROPOSED]` (built by Claude, open to the maintainer's review) unless marked otherwise. Admin-facing guides live in `docs/`
(`install.md`, `reverse-proxy.md`, `customizing.md`).

## Web foundation (TD-05, TD-25)

- **Vendored, no build step** (NFR-OFF-1): Pico CSS 2.1.1 (the default class-based build; the classless and conditional builds lose
  `.container` and the dropdown classes), htmx 2.0.11, the Barlow Condensed font (OFL, headings, brand and big numbers only; tables use the
  system font stack). Pinned versions and licences in `web/static/il2ks/vendor/`.
- **Look** (maintainer: "vaguely military, nothing distracting"): olive / khaki / gunmetal neutrals with a muted amber accent, light and dark
  (follows the OS, toggle remembered in localStorage, `?theme=dark|light` for one view), squared 3 px corners, thin rules. Texture (a low-
  contrast camo tile) only in the header band, the home hero, empty states and error pages, never behind data. Every colour is a `--il2-*`
  token defined once with `light-dark()` (needs Chrome 123+, Firefox 120+, Safari 17.5+). `SiteSettings.accent_color` overrides the accent.
- **Template contract** (semi-public API, TD-25): `base.html` blocks `title`, `head`, `nav`, `content`, `footer`, `scripts`; views set
  `page_title`. The `site` context processor adds `site` (SiteSettings or unsaved defaults, never a write), `logo_url`, `site_links` (unsafe
  schemes dropped), `accent_css`, `data_updated`, `il2ks_version`; it costs two primary-key reads, the data-version one shared with the
  caching middleware.
- **Components** (`templates/il2ks/components/`, each documents its context; `{% load il2ks %}`): formatting filters (`duration`, `utc`,
  `num`, `ratio`, `per_hour`, `percent`: a zero denominator gives "—"), `side`, badges (coalition, outcome, fate, status, aircraft status,
  combat role, generic), `icon` (inlines an SVG from static, so `custom/static` overrides work), `aircraft_icon` (per-type file, else
  generic jet/prop by propulsion), `stat_tile`, `kv_list`, `breadcrumbs`, `dropdown`, `notice` (hidden / may still change / info / warning),
  `accordion`, and the **list pattern**: `results_region` + `filter_bar` + `filter_select` / `filter_text live=True` + `sort_th` +
  `pagination`. htmx requests return the full page and swap `#results` (`hx-select`), so there are no partial templates and everything works
  without JavaScript. Sort fields are whitelisted by the view.
- **Labels:** the sortie `disconnected` flag reads "Left the server"; "Destroyed" comes only from `aircraft_status`, never from
  `damage_taken`.
- **Style guide** at `/_styleguide/` (only with DEBUG) shows every component; English-only by design.
- **Placeholders** for every icon and image named in [15_visual_assets.md](15_visual_assets.md), under `web/static/il2ks/img/`.
- **Free icon option** (research, 2026-10-03): Tabler Icons (MIT, outline, 24 px grid) covers almost every slot (tank, parachute, prison,
  plug, propeller…); game-icons.net (CC BY 3.0) is more military but filled and detailed, so it doesn't mix with Tabler at 16 px. Paid
  stock marketplaces (Envato, Flaticon, Freepik, Icons8, Iconfinder, IconScout, Creative Market, Shutterstock, Adobe Stock) forbid
  redistributing extractable files, which an open-source repo and a PyPI wheel always do, and a stricter licence of our own on the assets
  doesn't fix that; only Streamline Premium explicitly allows open-source use (with attribution, ≤ 100 icons). Commissioned work under a
  licence we choose is the clean route. Candidates and a contact sheet were prepared outside the repo; adopting Tabler is **OQ-37**. **Applied** (2026-10-03): 50 UI icons are Tabler Icons 3.48.0 (outline,
  normalised to 24 px / `currentColor` / stroke 2); the file → icon table is in `static/il2ks/img/README.md`, the licence in `NOTICE`;
  `tests/unit/test_icon_files.py` checks every referenced icon exists and is well-formed. Icon picks: OQ-60.

## Pages (as built, 2026-10-03)

- **Home**: site description, player search, the last mission (tiles, sorties per side, top 5 pilots by air then ground kills), the
  latest 8 missions. Empty missions are left out.
- **Mission list**: newest first, 25 per page, sortable; filters: name (live), period, winner, empty missions (hidden by default). Titles
  come from the mission file name ("The Sinuiju Bridges 1951").
- **Mission detail**: tiles, one sortie table per side (mission clock, pilot, aircraft, combat role, outcome, fate, kills, flight time),
  the PvP kill list. A hidden player keeps an **anonymised row** ("Hidden player", no links) so the mission's numbers still add up (gut
  call on FR-ADM-3's "gone from rosters").
- **Player search**: live search on current and past names ("also known as"), recently active players when empty, sortable.
- **Player profile**: header with past names, tiles with ratios, the collapsible ground-kill breakdown, ratios and other totals, the
  hall of shame (taxi accidents, strafed), per-aircraft table (links to the filtered sortie list), the 10 latest sorties. Gunner-only
  players get a notice. **K/D, K/L and kills per sortie/hour use air kills only** (ground kills include fences; they get their own
  per-sortie figure), OQ-38. Elo isn't shown (pages deferred).
- **Whole-row links** (FR-WEB-24, 2026-10-03): put `class="stretched-link"` on a row's main `<a>`; pure CSS (`tr:has(.stretched-link)`,
  the link's `::after` covers the row, every other link in the row is lifted above it automatically), hover background and an inset
  focus outline. Needs `:has()` (Chromium, Firefox 121+, Safari 15.4+; the site already needs `light-dark()`). Used on: home missions and
  top pilots, mission list, mission-detail sortie rows (→ sortie, the pilot name stays a profile link; hidden players get no row link),
  player search, profile recent sorties and per-aircraft rows, player sortie list. Kill, damage and timeline tables have no single target
  and stay as they are.
- **Flavor text** (FR-WEB-23, 2026-10-03): `web/flavor.py` `SPOTS` maps a spot to translatable variants; `{% flavor "spot" seed %}` picks
  one by SHA-256 of `spot:seed` (stable across restarts and languages, so caching holds); `{% sortie_flavor %}` picks the sortie spot by
  priority taxi accident > friendly fire > captured > ditched > shot down by AA > 3+ air kills > landed with ≥ 50% damage (none for gunners
  or ordinary sorties). Spots: hall of shame (with incidents / clean), home top pilots (doesn't name the pilot), home "nobody scored",
  sortie page. Quiet italic `.flavor` style. Placement and wording: OQ-53.
- **Sortie map** (FR-WEB-12, 2026-10-03): an accordion (open) on the sortie page with a server-rendered inline SVG of the key events
  from the stored timeline (+0 queries): numbered markers with the event icons, a faint dashed line in time order (labelled "not the
  flight path"), a km grid in absolute game coordinates, an N arrow, a legend, `<title>` tooltips; the timeline table is the textual
  equivalent. Axes: game `x` = north, `z` = east (IL-2 convention; x checked against airfield positions in the samples, z assumed).
  Pure geometry in `web/map_geometry.py`, view model in `web/sortie_map.py`. **Map images drop in** as
  `static/il2ks/img/maps/<map-id>.webp|png|jpg|svg` with bounds in `maps.json` (overridable in `custom/static`); placeholder bounds for
  `korea` are 0–512 km. Until a mission records its map, the id is `korea`. Events with missing, origin or off-map positions are skipped
  and counted. Which events are drawn: OQ-54.
- **Killboard and ironman streaks** (FR-WEB-9, 2026-10-03): level-2 `PlayerKillboard` (two mirror rows per pair: kills, deaths, last
  encounter) and `PlayerStreak` (current and best streak: sorties, air kills, flight time), rebuilt per affected player in
  `recompute_players` (incremental == rebuild, checked on 45 sample missions). Pure streak rule in `core/streaks.py`. Pages: profile
  sections (Ironman; Killboard top 5 each way), `/players/<pk>/killboard/`, `/streaks/` (running streaks), a home block of 5. The
  sections read through simple template tags (`il2ks_boards`), not the view context. The streak list filters on "ended within 30 days
  of now", so between ingests a cached home page can lag by up to one ingest interval (accepted). Rules: OQ-56, OQ-57, OQ-58.
- **Charts** (FR-WEB-16, 2026-10-03): server-rendered inline SVG bar charts, no JS: pure layout in `web/charts.py`
  (`build_bar_chart(ChartSpec)`: 1/2/5 ticks, k/M abbreviations, legend from 2 series), `{% bar_chart spec %}` with `role="img"`, title/desc,
  per-bar `<title>`, a "Show the numbers" table and an empty state; colours `--il2-chart-1/2` (steel blue, rust) checked for contrast in
  both themes. Home: sorties per day for the 30 days ending at the newest active day (level-2 `ActivityDay`, UTC days by mission start,
  hidden missions excluded and refreshed when an admin hides one). Profile: sorties per tour and air kills/deaths per tour (from
  `PlayerTour`, with ≥ 2 tours, latest 12). +1 query on each page. Choices: OQ-59.
- **Score and leaderboards** (FR-WEB-7/19/20, 2026-10-03): pure `score_sortie(SortieFacts, ScoreRules)` in `core/ratings/score.py` gives
  an air and a ground score per pilot sortie (gunners 0), stored as `PlayerSortie.air_points/ground_points` and summed into the counter
  tables (`score_air`, `score_ground`, `score_ground_attack`). Rules and leaderboard minimums are the `[score]` config section; a rule
  change applies with `il2ks rebuild-aggregates` (scores read only stored columns; 6.6 s for 210 missions). Ground per hour on target is a
  read-time F-expression. Boards: `/leaderboards/<air|ground|ground-hour|kills|elo-prop|elo-jet>/` with `?tour=`, `?aircraft=` (per-type
  rows), sort and paging; hidden players never appear; ≤ 6 queries. Profile block `players/detail_scores.html` (all-time). Not built:
  prop/jet and fighter/attack splits for the score boards (need more level-2 rows), per-type Elo. Values: OQ-62..64.
- **Tours on pages** (2026-10-03): `?tour=<Tour.pk>` on the profile (totals, tiles, ratios, ground kills, hall of shame, per-aircraft
  table and recent sorties follow it), the player sortie list and the mission list. No value or an unknown one means all time (200, so
  stale shared links keep working). Views use `queries.tours.tour_choice_from(request.GET)` (one query); templates use
  `{% tour_select %}` (swaps `#main`, works without JS) or a `filter_select` inside a filter bar. Titles are localised at display time
  (`tour_title`: "Month YYYY" via `YEAR_MONTH_FORMAT`, "Tour N" via gettext; anything else is an admin rename, shown as is). Elo and
  scores stay all-time. Product defaults: OQ-45..48.
- Query budgets: home ≤ 4, mission list 5, mission detail 5, search 4, profile 7 (8 with a tour), sortie list 7 (incl. the 2
  context-processor reads; the tour list costs one).
- Coalition emblems: `{% coalition_badge %}` uses the site-settings choice (neutral by default, or placeholder insignia drawn as plain
  shapes: VVS, PLAAF, KPAF, USAF, ROKAF, UN).

## Admin (FR-ADM-1..5)

- The admin lives in the `web` app (`web/admin.py`): models stay in `db/`. Rows that ingest owns (players, missions, sorties, counters,
  runs) are read-only apart from `is_hidden`; nothing ingested can be added or deleted there.
- **Site settings** singleton (title, server name, description, accent colour with a picker, links as `Label | https://url` lines, REDFOR /
  BLUFOR names and emblems, logo upload; FR-ADM-2 has the upload rules as built).
- **Hiding** (FR-ADM-3, `[DECIDED]`): bulk hide/unhide actions; presentation only. `Player.objects.visible()` / `Mission.objects.visible()`.
- **Names** (FR-ADM-5): game object and country display names editable inline, with "reset to catalog default".
- **Ingestion status** page `/admin/ingestion/` and a read-only run list (FR-ADM-4 as built).
- A small local generic subclass makes `ModelAdmin[Model]` work at runtime (django-types makes it generic for the checker only), instead
  of patching Django (TD-16).

## Caching (TD-28)

See TD-28 "as built": a 304 before the view runs, ETag from data version + language + il2ks version + process start + path + `HX-Request`.

## Serving (TD-10, TD-11, TD-23)

- **Settings come from the config** (`il2ks.toml` + `IL2KS_<SECTION>_<KEY>` env), loaded by `settings.py` itself; the rules are pure
  functions in `serving/djsettings.py`. Keys: top-level `debug`; `[web] host` (127.0.0.1), `port` (8000), `workers` (1; Windows supports
  only 1), `threads` (4), `allowed_hosts`, `secret_key`; `[https] mode` (`caddy` / `external`), `domain`, `email`, `cert` (`auto` /
  `internal`), `caddy_path`, `http_port`, `https_port`, `hsts_seconds` (86400).
- **Secret key** (NFR-SEC-2): `[web] secret_key`, else `<data dir>/secret_key.txt` (generated by `setup`, `web` or `run`), else a dev
  placeholder that `web` refuses to serve with outside debug.
- **Production** (debug off): proxy SSL header, SSL redirect, HSTS 1 day (no subdomains, no preload; configurable), secure + HttpOnly
  cookies, nosniff, `same-origin` referrer policy. `ALLOWED_HOSTS` = domain + extras + localhost; with neither it's `*` and doctor warns.
  Static files: WhiteNoise compressed manifest storage (hashed names), lenient so a missing file is a 404, `collectstatic` at web start.
- **Debug** (`debug = true`, `IL2KS_DEBUG=1` or `il2ks web --dev`): plain http, `runserver`, WhiteNoise reads source folders.
- **`il2ks web`**: granian (WSGI) on `[web] host:port`; migrates first (with the pre-migration backup), then `collectstatic`.
- **`il2ks run`**: migrates once, then supervises `web`, `watch` (when a log folder is set) and Caddy (not in `external` mode or debug),
  restarting crashed children with 1→60 s backoff and stopping them politely (Ctrl+Break in a separate process group on Windows, then a
  15 s grace period). A second `run` for the same data dir exits with code 3. On Windows a plain signal handler can't interrupt an untimed
  wait, so `web` installs a console-control handler and `watch` waits in 0.5 s slices.
- **Caddy**: the Caddyfile is generated on every `run` into `<data dir>/caddy/` (never hand-edited). Domain → automatic HTTPS; an IP
  address → Let's Encrypt's 6-day IP certificates via the `shortlived` ACME profile (generally available since January 2026; Caddy 2.10+
  orders them only when asked; IPv6 not verified); no domain → `tls internal` with a loud "testing only" warning. Caddy runs with the admin
  API off, never touches the machine's trust store, and keeps its state in the data dir (so certificates are backed up). Lookup:
  `caddy_path`, then PATH, then `<data dir>/bin/caddy(.exe)`. `il2ks caddyfile` prints it.
- **Own proxy** (`external`): sample nginx, IIS (URL Rewrite + ARR) and Apache configs in `docs/reverse-proxy.md`.

## Customization (TD-25, FR-ADM-6)

**Template versions** (as built): every built-in template, stylesheet and script starts with `{# il2ks-template: <path> vN ... #}` (CSS/JS:
`/* ... */`); `src/il2ks/web/template_versions.json` records version + content hash; a test fails on a change without a bump; `il2ks dev
bump-templates` bumps; `il2ks dev template-changes <tag>` lists bumps for release notes (`docs/releasing.md`). Override states: current,
outdated, newer, unversioned, orphan, custom-only, unchecked (no version: images, vendored, Django's own). Problems appear as a red banner
on every admin page (staff only, never public), a start-up warning, `il2ks doctor`, and `il2ks custom list`; `il2ks custom diff` and
`custom accept` help update. `il2ks.web` is first in `INSTALLED_APPS` so its `admin/base_site.html` wins.

`custom/templates` and `custom/static` are always first in the lookup (created by `web`). `il2ks custom copy <path>` copies a built-in file
and records the original's hash in `custom/.il2ks-overrides.json`; `custom list` and `custom accept` complete it. Doctor warns when an
original changed since it was copied, disappeared, or when a hand-placed file shadows a built-in one it can't check.

## Operations (FR-OPS-1, 2, 6)

- **`il2ks setup`**: interactive (with defaults) or `--non-interactive` (flags or env for everything). Asks for the data dir, the log folder
  (bounded search for folders holding `missionReport(*)[*].txt` on Windows drives and Wine prefixes; candidates newest first), the server
  timezone (always asked on Windows, which has no IANA name), domain and HTTPS mode, admin account. Writes `il2ks.toml` from the shipped
  template keeping its comments, refuses to overwrite without `--force` (then keeps a dated copy and the server UID), creates the data dir
  and secret key, migrates, creates the admin, prints next steps.
- **`il2ks createadmin`**: create or reset an admin; Django's password validators; non-interactive via `IL2KS_ADMIN_PASSWORD` or a file.
- **`il2ks doctor [--json]`**: read-only checks grouped ERROR / WARN / OK, each with a fix. Exit 0 all OK, 1 warnings, 2 errors. Checks:
  config, data dir, database and migrations, log folder (and the text-log hint, OQ-1), Windows timezone, server UID, disk space (WARN < 1
  GiB, ERROR < 200 MiB), ingestion health, backups, secret key, debug, domain, Caddy, ports 80/443/web (ours via `run.json` is fine),
  static files, external-mode hints, `custom/` overrides.
- **Backups** (FR-OPS-6): `il2ks backup` → `<data dir>/backups/il2ks-backup-YYYYMMDD-HHMMSS.zip` (UTC) with a SQLite online-backup
  snapshot, the config, `server_uid.txt`, `secret_key.txt`, `custom/`, `media/` and a manifest; not the mission archives (large, kept
  forever, back them up separately). Keeps `[backup] keep` (10). Automatic **before pending migrations** (no migration if the backup fails)
  and **daily** from `watch` (`[backup] daily = true`; a failure retries an hour later). `il2ks restore <zip>` validates, refuses while a
  writer holds the lock or a newer il2ks made the backup, warns if the site seems to be running, takes a safety backup, swaps in, verifies.
  SQLite only.
- **Autostart for the manual path**: `il2ks service systemd` (Linux unit) and `il2ks service schtasks` (Windows task XML with restart on
  failure) print or write the definition; only `--install` changes the machine. A real Windows service is the installer's job.
- **Exit codes** (all commands): 0 ok, 1 partial failure / warnings, 2 usage or config error, 3 lock held.

## Packaging and release (TD-14)

- Hatchling, version single-sourced from `il2ks.__version__`; the wheel carries templates, static files (incl. vendored), catalog data,
  the example config and migrations (an `artifacts` rule keeps files `.gitignore` would drop). `tests/unit/test_packaging.py` checks the
  config, and with `IL2KS_TEST_BUILD=1` builds and inspects the real wheel.
- Release workflow (`.github/workflows/release.yml`, not run yet): on a `vX.Y.Z` tag, check the tag against `__version__`, test, build,
  smoke-test the wheel in clean venvs on Ubuntu and Windows, publish via PyPI trusted publishing. **One-time setup for the maintainer:** add
  the trusted publisher on pypi.org (repo `il2_korea_stats`, workflow `release.yml`, environment `pypi`) and create the `pypi` environment on
  GitHub.

## Proposed, not decided

- Doctor thresholds (reports stale after 7 days; backups stale after 3 days, or 30 with daily off).
- `[backup] daily` switch; backup names in UTC.
- Commands other than `web`/`run` accept the dev secret key (they never sign anything).
- HSTS preload and `includeSubDomains` stay off (no switch until someone asks).
