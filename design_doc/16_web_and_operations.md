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
  token defined once with `light-dark()` (needs Chrome 123+, Firefox 120+, Safari 17.5+); admins retheme it in the Site settings (Branding,
  below).
- **Template contract** (semi-public API, TD-25): `base.html` blocks `title`, `head`, `nav`, `content`, `footer`, `scripts`; views set
  `page_title`. The `site` context processor adds `site` (SiteSettings or unsaved defaults, never a write), `logo_url`, `nav_links` / `site_links` (read from
  `SiteSettings.links`, no extra query), `theme_css`, `data_updated`, `il2ks_version`. **Upgrade note (2026-10-04):** `accent_css` and `SiteSettings.accent_color` are gone (replaced by `theme_css` and `SiteSettings.theme`), so a `custom/` `base.html` override silently loses the accent colour (the template-version bump warns, TD-25); release notes must say so. It costs two primary-key reads, the data-version one
  shared with the caching middleware.
- **Components** (`templates/il2ks/components/`, each documents its context; `{% load il2ks %}`): formatting filters (`duration`, `utc`, `local_*`,
  `num`, `ratio`, `per_hour`, `percent`: a zero denominator gives "—"), `side`, badges (coalition, outcome, fate, status, aircraft status,
  combat role, generic), `icon` (inlines an SVG from static, so `custom/static` overrides work), `aircraft_icon` (per-type file, else
  generic jet/prop by propulsion), `stat_tile`, `kv_list`, `breadcrumbs`, `dropdown`, `notice` (hidden / may still change / info / warning),
  `accordion`, and the **list pattern**: `results_region` + `filter_bar` + `filter_select` / `filter_text live=True` + `sort_th` +
  `pagination`. htmx requests return the full page and swap `#results` (`hx-select`), so there are no partial templates and everything works
  without JavaScript. Sort fields are whitelisted by the view.
- **Compatibility and safety** (review fixes, 2026-10-04): removed filters stay as deprecated aliases (`utc_date` = `local_date`, `utc_short` =
  `local_short`) so old `custom/` overrides keep compiling; SVG chart numbers are formatted locale-independently (a decimal comma is an
  invalid SVG length); list queries defer the big `PlayerSortie` JSON columns (`HEAVY_SORTIE_COLUMNS`: ammo, damage breakdown, timeline), only
  the sortie report loads them; migration backfills call `rebuild_aggregates` only through `ops.migrate._rebuild_all` (every config section).
  Migrations 0011 and 0027 run `SET CONSTRAINTS ALL IMMEDIATE` on Postgres after their data step (the only raw SQL in migrations; TD-19's
  exception, needed to fire deferred FK checks) and are atomic.
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

- **Home**: site description, player search, "Online now" (`/live/` fragment), the last mission (tiles, sorties per side, top 5 pilots by air
  then ground kills), the latest 8 missions (empty missions left out), the top 5 of the **Elo jet, Elo prop and ground-per-hour** boards (all
  time, OQ-64), a streaks block of 5 and the activity chart.
- **Mission list**: newest first, 25 per page, sortable; filters: name (live), period, winner, empty missions (hidden by default). Titles
  come from the mission file name ("The Sinuiju Bridges 1951").
- **Mission detail**: tiles, one sortie table per side (mission clock, pilot, aircraft, combat role, outcome, fate, kills, flight time),
  the PvP kill list. A hidden player keeps an **anonymised row** ("Hidden player", no links) so the mission's numbers still add up (gut
  call on FR-ADM-3's "gone from rosters").
- **Player search**: live search on current and past names ("also known as"), recently active players when empty, sortable.
- **Player profile**: header with past names, tiles with ratios, the collapsible ground-kill breakdown, ratios and other totals, the
  hall of shame (taxi accidents, strafed), per-aircraft table (links to the filtered sortie list), the 10 latest sorties. Gunner-only
  players get a notice. **K/D, K/L and kills per sortie/hour use air kills only** (ground kills include fences; they get their own
  per-sortie figure), OQ-38. Score and Elo have their own block (`players/detail_scores.html`; scores follow the tour, Elo stays all time); the per-type Elo is in the
  per-aircraft table.
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
- **Sortie map** (FR-WEB-12, 2026-10-03; **not on main**: benched until after the release, OQ-54/55, the code stays on its branch): an accordion (open) on the sortie page with a server-rendered inline SVG of the key events
  from the stored timeline (+0 queries): numbered markers with the event icons, a faint dashed line in time order (labelled "not the
  flight path"), a km grid in absolute game coordinates, an N arrow, a legend, `<title>` tooltips; the timeline table is the textual
  equivalent. Axes: game `x` = north, `z` = east (IL-2 convention; x checked against airfield positions in the samples, z assumed).
  Pure geometry in `web/map_geometry.py`, view model in `web/sortie_map.py`. **Map images drop in** as
  `static/il2ks/img/maps/<map-id>.webp|png|jpg|svg` with bounds in `maps.json` (overridable in `custom/static`); placeholder bounds for
  `korea` are 0–512 km. Until a mission records its map, the id is `korea`. Events with missing, origin or off-map positions are skipped
  and counted. Which events are drawn: OQ-54.
- **Killboard and ironman streaks** (FR-WEB-9 / FR-WEB-25, built 2026-10-03, extended 2026-10-04): level-2 `PlayerKillboard` (two mirror rows
  per pair: kills, deaths, assists, last encounter) and `PlayerTourKillboard` (the same per tour), `PlayerStreak` (current and best streak:
  sorties, air kills, flight time) and `PlayerBestStreak` (best streak by sorties survived, air kills and flight time, all time and per
  tour; a tour streak counts only that tour's sorties), rebuilt per affected player in `recompute_players` (incremental == rebuild, checked
  on 45 sample missions). Pure streak rule in `core/streaks.py`. Pages: profile sections (Ironman; Killboard top 5 each way),
  `/players/<pk>/killboard/` (`?tour=`, `?sort=`), `/players/<pk>/streaks/` (the player's best streaks, a sub-page, `?tour=`), `/streaks/`
  (running streaks), a home block of 5. The sections read through simple template tags (`il2ks_boards`), not the view context. The streak
  list filters on "ended within 30 days of now", so between ingests a cached home page can lag by up to one ingest interval (accepted).
  **`[killboard] assists`** (default false): assist credits get their own column; the value is stored in `SiteSettings.killboard_assists` by
  `rebuild-aggregates` and takes effect with it. Hidden opponents sort last. Rules: OQ-56..58, OQ-81..83.
- **Charts** (FR-WEB-16, 2026-10-03): server-rendered inline SVG bar charts, no JS: pure layout in `web/charts.py`
  (`build_bar_chart(ChartSpec)`: 1/2/5 ticks, k/M abbreviations, legend from 2 series), `{% bar_chart spec %}` with `role="img"`, title/desc,
  per-bar `<title>`, a "Show the numbers" table and an empty state; colours `--il2-chart-1/2` (steel blue, rust) checked for contrast in
  both themes. Home: sorties per day for the 30 days ending at the newest active day (level-2 `ActivityDay`, UTC days by mission start,
  hidden missions excluded and refreshed when an admin hides one). Profile: sorties per tour and air kills/deaths per tour (from
  `PlayerTour`, with ≥ 2 tours, latest 12). +1 query on each page. Choices: OQ-59.
- **Score and leaderboards** (FR-WEB-7/19/20, 2026-10-03, extended 2026-10-04): pure `score_sortie(SortieFacts, ScoreRules)` in
  `core/ratings/score.py` gives an air and a ground score per pilot sortie (gunners 0), stored as `PlayerSortie.air_points/ground_points` and
  summed into the counter tables (`score_air`, `score_ground`, `score_ground_attack`). Rules (doc 13 "Score") and leaderboard minimums are the
  `[score]` config section; a rule change applies with `il2ks rebuild-aggregates` (scores read only stored columns; 6.6 s for 210 missions).
  Ground per hour on target is a read-time F-expression. Boards: `/leaderboards/` (air score) and `/leaderboards/<air|ground|ground-hour|
  kills|elo-prop|elo-jet>/` with `?tour=`, `?aircraft=` (per-type rows, and per-type Elo), `?pool=prop|jet` (score and kill boards, from
  `PlayerPool` / `PlayerTourPool`; a chosen aircraft type overrides the pool; Elo boards have no pool filter), sort and paging; hidden players
  never appear. Tabs are grouped: **Fighters** (air, Elo prop, Elo jet), **Attack** (ground, ground per hour), **General** (kills). Home: top
  5 of Elo jet, Elo prop and ground per hour (all time). Profile block `players/detail_scores.html` (scores follow the selected tour; Elo stays all time, labelled). Values and product choices:
  OQ-62..64, OQ-67, OQ-84..86.
- **Aircraft stats** (FR-WEB-8, 2026-10-03): `/aircraft/` lists flown types (prop/jet, side, sorties, pilots, flight time, kills, deaths,
  losses, K/D, K/L, survival, attack share, hits to destroy; sortable); `/aircraft/<pk>/` adds matchups vs each enemy type, **top pilots** (hidden
  players left out; by per-type Elo, and ground score per hour on target for attack work, under the leaderboard minimums; types with an attack
  share of 50% or more list the ground ranking first), hits to destroy per ammo, loadouts. Level-2 `AircraftStats` / `AircraftMatchup` /
  `AircraftPayload`, built by `ingest/aircraft_stats.py` (incremental == rebuild). All-time only. Rules: OQ-65.
- **Stat highlights** (FR-WEB-22, 2026-10-03): level-2 `StatThreshold` rows (p10/p25/p50/p75/p90, linear
  interpolation) per metric, all-time and per tour, over players with ≥ `[marks] min_sorties` (20) sorties, only when ≥ 20 pilots qualify;
  recomputed per saved mission (a few ms) and by rebuild-aggregates. Percentiles, not mean + 2σ: the ratios are skewed with a floor at 0
  and survival is capped at 100%. `{% stat_mark "key" %}` on the profile's ratios list: above p90 "Top 10%" (accent), above p75 "Top
  25%" (muted), strict `>`; low values are never marked. Real data (216 pilots with ≥ 20 sorties, September 2026): survival p50 74% /
  p90 87%; K/D p50 0.34 / p90 2.96; K/L 0.26 / 1.80; air kills per sortie 0.09 / 0.48, per hour 0.37 / 1.71; ground kills per sortie
  2.9 / 13.7. +1 query on the profile. Rules: OQ-66.
- **Tours on pages** (2026-10-03, current-tour default 2026-10-04): the profile (totals, tiles, ratios, ground kills, hall of shame,
  per-aircraft table and recent sorties follow the tour), the mission list, the player's sortie list, the killboard and the leaderboards have a
  tour selector and **open on the current tour** (the newest `Tour` row, data-only so caching and ETags stay valid) when `?tour` is absent
  or unknown; `?tour=all` is all time; ids over 18 digits count as unknown (stale links still answer 200). A segmented toggle (current tour /
  All time) sits next to the select, with "Next tour starts <local time>" beside it (hidden in manual mode, and by JS once past). A tour
  without sorties shows the `tour_empty` flavor text. Views use `queries.tours.tour_choice_from(request.GET)` (one query); templates use
  `{% tour_select %}` (swaps `#main`, works without JS) or `{% tour_filter %}` on the list pages (segmented toggle + select). Titles are localised at display time
  (`tour_title`: "Month YYYY" via `YEAR_MONTH_FORMAT`, "Tour N" via gettext; anything else is an admin rename, shown as is). Elo stays
  all-time. Choices: OQ-45..48, OQ-78..80.
- **Local times** (FR-WEB-17, TD-15): `localtime.js` formats every `<time>` with `Intl.DateTimeFormat` (`dateStyle: medium`, `timeStyle:
  short`) in the page language and the browser's zone; the zone is named only in the footer; the UTC time stays in the tooltip.
- **Setup page** (`/setup/`, installer path): answers 404 unless a setup token file exists (`il2ks web` / `run` write it while no admin exists),
  the request is straight from this machine (loopback peer, no proxy headers, `localhost` Host) and the URL carries the token (constant-time
  check, guesses counted, then locked out). On submit it writes the config via `ops.setup.complete_web_setup`, creates the admin and deletes the
  token; from then on 404. The image disables it (`IL2KS_SETUP_PAGE=off`, OQ-70). Code: `web/views/setup.py`, `serving/setup_token.py`.
- **Query budgets** (TD-22; a test per page, shared constants in `tests/simple_reads.py`; every number includes the 2 context-processor reads;
  never raise one without a reason in the test). Home 10 (9 with no missions; `HOME_READS`, `HOME_READS_EMPTY`: the 4 extras are the three
  compact boards and the online-now snapshot), mission list 5, mission detail 5, player search 4, profile 12 all time (`PROFILE_READS_ALL_TIME`)
  and 13 for a tour, which includes the default current tour (`PROFILE_READS_TOUR`: + the `PlayerTour` row), player sortie list 7, sortie
  detail 7, killboard 7, best streaks 5, streak list 6, leaderboards 6 (7 with tour + pool; the Elo boards 4), aircraft list 4, aircraft detail
  9, live fragment 3 to 5. The `tests/perf/` suite (doc 08) has its own, looser per-page limits over a larger seeded world (N+1 guard).
- Coalition emblems: `{% coalition_badge %}` uses the site-settings choice (neutral by default, or placeholder insignia drawn as plain
  shapes: VVS, PLAAF, KPAF, USAF, ROKAF, UN).

## Admin (FR-ADM-1..5)

- The admin lives in the `web` app (`web/admin.py`): models stay in `db/`. Rows that ingest owns (players, missions, sorties, counters,
  runs) are read-only apart from `is_hidden`; nothing ingested can be added or deleted there.
- **Site settings** singleton (branding below; REDFOR / BLUFOR names and emblems; logo upload, FR-ADM-2 has the upload rules as built).
- **Hiding** (FR-ADM-3, `[DECIDED]`): bulk hide/unhide actions; presentation only. `Player.objects.visible()` / `Mission.objects.visible()`.
- **Names** (FR-ADM-5): game object and country display names editable inline, with "reset to catalog default".
- **Ingestion status** page `/admin/ingestion/` and a read-only run list (FR-ADM-4 as built).
- A small local generic subclass makes `ModelAdmin[Model]` work at runtime (django-types makes it generic for the checker only), instead
  of patching Django (TD-16).

## Branding (TD-25, FR-ADM-2) `[PROPOSED]`

Branding is data, not code. Server admins edit in Site settings: title, server name, description, logo, heading and body fonts, a color theme,
and an ordered list of extra navigation links. Navigation links are `NavLink` rows (label, http/https URL, optional built-in icon, position)
edited inline; the save renumbers them and publishes a copy into `SiteSettings.links` (no extra query). The old "Links" setting is folded in
and migrated. Links open in a new tab with `rel="noopener noreferrer"`. The menu wraps instead of overflowing; we recommend at most 3
short-labelled links (measured: 3 fit at >= 1280 px, 5 at 768 px, 2 at 360 px). Every color in the CSS is a `--il2-*` token in `site.css`
`:root` (light and dark via `light-dark()`); a unit test forbids colors elsewhere. `SiteSettings.theme` stores only overrides per mode;
`il2ks.web.theme.theme_css` emits them from validated `#RRGGBB` values and fixed font stacks (no CSS injection). The admin warns (never
blocks) on WCAG contrast below 4.5:1 (charts 3:1). Presets: Steel blue, Desert sand, High contrast, Default. Fonts: chosen by key from bundled
and system stacks, no third-party font host; `.woff2` upload is in progress. Every branding save bumps the data version (TD-28). Product
choices: OQ-87, OQ-88.

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
