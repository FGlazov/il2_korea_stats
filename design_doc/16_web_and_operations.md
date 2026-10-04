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
  combat role, generic, and the **pilot fate** badge Dead / Captured / Survived with the stored fate as a tooltip or note; `[DECIDED]` for now, a designer may restyle it later, OQ-106), `icon` (inlines an SVG from static, so `custom/static` overrides work), `aircraft_icon` (per-type file, else
  generic jet/prop by propulsion), `stat_tile`, `kv_list`, `breadcrumbs`, `dropdown`, `notice` (hidden / may still change / info / warning),
  `accordion`, `columns_picker` (the optional-columns control, below), `language_menu`, and the **list pattern**: `results_region` + `filter_bar` + `filter_select` / `filter_text live=True` + `sort_th` +
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

## Column descriptions (maintainer request 2026-10-04) `[DECIDED]`
Table headers whose meaning isn't obvious (Elo, time on target, accuracy, K/L, assists, interception...) carry a short description.
- **One mechanism**: `{% sort_th ... hint=X %}` and `{% col_th label numeric=True hint=X %}` (`templatetags/il2ks.py`, components `col_th.html`,
  `col_hint.html`). `X` is a key of `web/column_hints.py::HINTS` (the single place with the wording, with `Translators:` comments) or a ready
  text. Optional columns (`web/columns.py`) use `Column.description`: the column's own `hint`, else the `HINTS` entry with the same key as
  the column; the picker's tooltip and the header show the same text. Obvious columns (Name, Date, Aircraft, Sorties...) get none.
- **Rendering**: the header gets class `has-hint`, a dotted underline on the label and a small `?` marker. The text is a `role="tooltip"`
  element inside the `<th>`; the label (a plain header: a `tabindex=0` span, a sortable one: the sort link itself) has `aria-describedby` to
  it, so a screen reader reads it as the description and the link's name stays "Kills". The marker is `aria-hidden` and only serves pointers.
  No nested interactive elements; ids are `hint-<hash of the text>-<n>`, numbered per render, so a full page and an htmx region never clash.
- **Showing it**: CSS shows it on `:hover` and `:focus-within`; the tooltip is `position: fixed` (the scrolling `.table-wrap` would clip an
  absolute one) and `il2ks.js` places it under the header, clamped to the viewport (so it never causes sideways page scroll at 360 px).
  A tap on the marker toggles it (a tap on a sort link sorts); Escape hides it; a scroll or resize closes a tapped one. Colours are
  tokens, so both themes work. Tests: `tests/integration/test_column_hints.py`, `tests/e2e/test_column_hints.py`.
- Adding a column: use `hint=` with a new `HINTS` key (or name the optional column's key in `HINTS`), then `il2ks dev translations update`.

## Pages (as built, 2026-10-03)

- **Home** (tour-aware: `/?tour=`, no `tour` = the current tour, `?tour=all` = all time, `[DECIDED]` maintainer, OQ-79): site description, player
  search, "Online now" (`/live/` fragment, always live), the last mission (tiles, sorties per side, top 5 pilots by air then ground kills), the
  latest 8 missions (empty missions left out), **six boards in a 3x2 grid** (2 columns on a tablet, 1 on a phone), the top 5 of **Elo jet, Elo prop,
  interception, ground score per hour, tank busting and play time** (`[DECIDED]` maintainer, OQ-64, OQ-104; the Elo boards are all time only, the
  rest follow the tour; titles and player names link with the page's scope, `?tour=<id>` or `?tour=all`), a streaks block of 5 (the longest streaks
  inside the tour) and the activity chart (the tour's own days). Elo games are called **encounters** in the UI (maintainer, 2026-10-04).
- **Pagination** `[DECIDED]` (maintainer, 2026-10-04, OQ-96: "100% paginate"; `queries/paging.py`): the mission list shows **10 missions** a page,
  every other long list **20 rows** (a player's sorties, players, leaderboards, killboard, streaks, achievement holders). The mission page paginates
  each coalition's sorties and the kills separately (`page_redfor`, `page_blufor`, ...), the sortie page its damage rows
  (`?page_damage=`); links keep every other parameter. **Not paginated, by design** (short or bounded lists): the aircraft list (one row per flown type) and the matchup, ammo and loadout tables on the aircraft page, the by-aircraft killboard tables (at most 60 enemy types), a player's best streaks and achievements pages and the achievements overview, the profile's fixed top-5 and latest-5 blocks, the sortie page's other tables, and the home page's blocks. **Exception** (maintainer, 2026-10-04): the sortie page's timeline is not paginated, every row is shown (a detail page, so it gets a higher server-time and query budget; the HTML stays lean because icons are a sprite). Real-log mission and sortie pages fell from 107-122 KB of HTML to
  87-95 KB (NFR-PERF-6).
- **Mission list**: newest first, 10 per page, sortable; filters: name (live), period, winner, empty missions (hidden by default). Titles
  come from the mission file name ("The Sinuiju Bridges 1951").
- **Mission detail**: tiles, one sortie table per side (mission clock, pilot, aircraft, combat role, outcome, fate, kills, flight time),
  the PvP kill list. A hidden player keeps an **anonymised row** ("Hidden player", no links) so the mission's numbers still add up (gut
  call on FR-ADM-3's "gone from rosters").
- **Mission page sortie tables** (2026-10-04, `[DECIDED]` for now, maintainer, OQ-113: the defaults are fine, to be revisited with the designer): the three sortie tables (REDFOR, BLUFOR, others) are sortable and take optional columns.
  One `?sort=` (`queries/missions.py::SORTIE_SORT_FIELDS`, a whitelist, `-` = descending, ties by spawn time, NULLs last, hidden players' rows last)
  orders all three; `?cols=` (`columns.MISSION_SORTIE_COLUMNS`: damage taken plus the player sortie list's extras, air / ground assists included)
  adds columns; unknown values are ignored; the kills table takes neither. One sortie query, the sort is done in SQL.
- **Player search**: live search on current and past names ("also known as"), recently active players when empty, sortable.
- **Player profile** (FR-WEB-4; reworked 2026-10-04: air and ground apart, shame and latest sorties near the top). Top to bottom: the header
  (past names), the tour selector, an in-page nav (plain anchors: Recent sorties, Air-to-air, Air-to-ground, Overall), the general tiles (sorties
  with the survival rate, flight time, deaths, planes lost with K/L), the **hall of shame** (taxi accidents, friendly-fire **kills**, a quip; a counted sortie with at least one friendly kill, hits and damage alone don't count, matching the sortie-page badge; the tile is named after kills, `[DECIDED]` maintainer, 2026-10-04, OQ-72, being applied), the
  **achievements medal row** (the highest earned tier of each achievement, linking to the sortie that earned it; links to the pilot's full list and
  the overview; nothing at all without a medal; doc 17), the **latest 5 sorties** (the heading and a "View all sorties" button open the player's
  sortie list with the page's `?tour=` scope), then three parts. **Air-to-air**: tiles, ratios with their stat marks, score and rating (air score,
  interception per hour, Elo prop and jet with games, all with marks), air kills by victim, the **killboard by aircraft type** (five types each
  way, "Full killboard") and the pilot killboard's top rows. **Air-to-ground**: tiles, the collapsible ground-kill breakdown, ground score,
  ground score per hour and **tanks per hour** on target, ground kills per sortie. **Overall**: ironman streaks, the per-aircraft table (links to
  the filtered sortie list, per-type Elo), PvE, "Other totals" (with strafed on the ground, which stays there for now; the maintainer reviews all pages later; `[DECIDED]` 2026-10-04, OQ-73; the sortie-page badge stays) and the per-tour charts. A part with no activity in the
  scope collapses to one muted line (`air_active` / `ground_active`; the type killboard still shows who shot the pilot down). Gunner-only players
  get a notice. **K/D, K/L and kills per sortie/hour use air kills only** (ground kills include fences; they get their own per-sortie figure),
  OQ-38. Partials: `players/detail_*.html`, listed at the top of `players/detail.html`; scores follow the tour, Elo stays all time.
- **Sortie page** (FR-WEB-6, extended 2026-10-04): the pilot fate as Dead / Captured / Survived with the stored fate as a note (`pilot_fate_badge
  sortie detail=True`); the timeline table has a **Damage** column with the signed percent of each hit row (+ given, − taken, from the `hit_given` /
  `hit_taken` rows, doc 13 "Timeline hits") and the ammo of the nearest hit; the ammo table dashes "Left" after a loss and explains why (doc 13);
  "Earned in this sortie" lists the medals (+1 query); the quip (below). Timeline **hit rows are tinted**: light green for a hit given, light red for a
  hit taken (a 9% mix of the `--il2-green` / `--il2-red` theme tokens in `sorties.css`, so both themes work). Air and ground assists are listed apart.
- **Optional columns** (FR-WEB-27, 2026-10-04): the player search, mission list, aircraft list and a player's sortie list keep their default
  columns in their templates and offer more through a "Columns" control (`components/columns_picker.html`, a plain GET form, works without JS).
  `web/columns.py` registers per list the optional `Column(key, label, cell)`; `?cols=a,b` (comma separated or repeated) picks them, unknown keys
  are ignored, they appear in registry order, and each key is also its `?sort=` key (the sort whitelists live in `queries/`; a test keeps both in
  step). Cells are plain text from counters the row already holds (+0 queries, TD-22); the page cache keys on the full URL, so the choice is
  shareable. Ratios sort in SQL with `queries.sorting.Ratio` (`numerator * scale / denominator`, optional `minus` for survival) and Elo with
  `Rated` (no value without a rated game); **undefined values (NULL) always sort last**, ascending or descending, on SQLite and Postgres. Players
  (all time only): Elo jet and prop, K/D, K/L, survival, PvP air kills, air and ground score, ground score per hour, planes lost, assists, friendly
  kills, first seen. Missions: friendly kills, tour, ended, REDFOR / BLUFOR sorties, sorties per player. Aircraft: PvP air kills, kills per hour,
  assists, bailouts, friendly kills, scores, ground per hour, sortie length, sorties per pilot. Player sorties: **Mission** (optional, `[DECIDED]` OQ-109), PvP and AI air
  kills, friendly kills, air and ground score, time on target, loadout, takeoffs, landings. The sortie list shows **damage taken by default** and
  the pilot fate next to the outcome (also on the mission page and the profile's latest sorties).
- **Achievements** (FR-WEB-26, doc 17; per tour since 2026-10-04, OQ-105): `/players/<pk>/achievements/` (every tier with its date, open tiers dimmed), `/achievements/` (all
  achievements with holder counts and the rarity per tier), `/achievements/<key>/?tier=N` (visible holders, newest first), all with the tour dropdown; medals and ribbons on the profile (shame entries in
  the hall of shame), "Earned in this sortie" on the sortie page, and the home page's **Recently earned** feed; icons in `static/il2ks/img/medal/` tinted per tier by `--il2-medal-*` tokens; words and display rows in `web/medals.py`.
- **Language menu** (FR-WEB-28, TD-24): a footer dropdown (`components/language_menu.html`, a Pico `details.dropdown`, closes on outside click
  and Escape through `il2ks.js`, works without JS) with the flag (decoration, `alt=""`) and the language's own name in its own `lang`; each language is a plain link,
  a GET to `/language/?language=<code>&next=<local url>`, which sets the cookie and redirects. Flags: flag-icons 7.5.0 (MIT),
  self-hosted SVGs (`img/flag/`, doc 15). No explicit choice means the browser's language (`LocaleMiddleware`).
- **Whole-row links** (FR-WEB-24, 2026-10-03): put `class="stretched-link"` on a row's main `<a>`; pure CSS (`tr:has(.stretched-link)`,
  the link's `::after` covers the row, every other link in the row is lifted above it automatically), hover background and an inset
  focus outline. Needs `:has()` (Chromium, Firefox 121+, Safari 15.4+; the site already needs `light-dark()`). Used on: home missions and
  top pilots, mission list, mission-detail sortie rows (→ sortie, the pilot name stays a profile link; hidden players get no row link),
  player search, profile recent sorties and per-aircraft rows, player sortie list. Kill, damage and timeline tables have no single target
  and stay as they are.
- **Flavor text** (FR-WEB-23, 2026-10-03): `web/flavor.py` `SPOTS` maps a spot to translatable variants; `{% flavor "spot" seed %}` picks
  one by SHA-256 of `spot:seed` (stable across restarts and languages, so caching holds); `{% sortie_flavor %}` picks the sortie spot with `flavor.sortie_spot`, the first match of: taxi accident, friendly kills, captured, shot down
  by an AI gunner, ditched, **strafed** (`sortie_strafed` parked, `sortie_strafed_landed` after a landing; doc 13 `[DECIDED]`, OQ-112), shot down by AA, **bomber hunter** (2+ air kills of bomber, attacker or transport class; `BOMBER_KILLS_MIN`), **ace** (3+
  air kills), **stolen kills** (**air** assists: 2+ with no air kill, 3+ with one, 6+ with two, below the ace line; maintainer 2026-10-04, OQ-107
  resolved, the variants reworded to fit pilots with kills), **stolen targets** (5+ **ground** assists, at least the own ground kills, under 70
  ground kills; `[DECIDED]` as built, maintainer, OQ-111), **battered victor** (landed, 50%+ damage taken, 2+ kills), limped home (the same damage, fewer kills), **ground pounder** (70+ ground
  kills), **quick first kill** (within 7 minutes of takeoff or an air start's spawn), **marathon** (1 hour or more of flight); none for gunners or
  ordinary sorties. The bomber and first-kill facts come from the timeline (`sortie_view.build_highlights`, `Highlights`); without it those two
  spots are skipped. Thresholds were read off the September 2026 archive (15,245 pilot sorties; each spot fires on 0.2% to 2% of them). Other
  spots: hall of shame (taxi only, friendly fire only, both, none: 3-4 variants each, and a separate warmer variant when a rate per sortie is
  strictly above the 90th percentile of pilots with at least `[marks] min_sorties` sorties in the same scope, all time or the selected tour; only
  the elevated kind is named, no ranking; `[DECIDED]` maintainer, 2026-10-04, OQ-74, wording still to be reviewed in `src/il2ks/web/flavor.py`),
  home top pilots (doesn't name the pilot), home "nobody scored", an empty tour. Quiet italic `.flavor` style. Two lines were replaced at the
  maintainer's request (2026-10-03; OQ-75): the POW "food" joke and "Ace-in-a-day territory" are gone, now "Out of the fight, but not out of the
  story." (sortie captured) and "Best showing of the mission. Well flown." (top pilot). Placement and wording: OQ-53, OQ-111, OQ-112.
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
  on 45 sample missions). Pure streak rule in `core/streaks.py`. Pages: profile sections (Ironman; **Killboard by aircraft** and Killboard, top 5 each way),
  `/players/<pk>/killboard/` (`?tour=`, `?sort=`; the by-aircraft tables of up to 60 enemy types sit above the player table), `/players/<pk>/streaks/` (the player's best streaks, a sub-page, `?tour=`), `/streaks/`
  (tour-aware, TD-26, built 2026-10-04: a tour dropdown; every visible pilot's best streak by sorties in the selected tour or all time from `PlayerBestStreak`, 20 per page as `page_best`, each row linking to `/players/<pk>/streaks/history/` with the same tour; below it the running streaks from `PlayerStreak`, `page_running`, shown on the current-tour and all-time views and hidden on a past tour because nothing is running in a finished tour `[PROPOSED]`), a home block of 5 whose "All streaks" button carries the block's tour (`?tour=`). The **killboard by aircraft type** (2026-10-04) is the level-2 `PlayerTypeKillboard` (per player, enemy
  type and scope: kills, deaths, and the player's own type most used in them; built by `ingest.type_board`, recomputed per affected player with
  the pair rows): "Aircraft shot down most" and "Aircraft that shot down this pilot most", all time or the selected tour, hidden opponents
  counted. The sections read through simple template tags (`il2ks_boards`), not the view context. The streak
  list filters on "ended within 30 days of now", so between ingests a cached home page can lag by up to one ingest interval (accepted).
  **`[killboard] assists`** (default false): assist credits get their own column; the value is stored in `SiteSettings.killboard_assists` by
  `rebuild-aggregates` and takes effect with it. Hidden opponents sort last. Rules: OQ-56..58, OQ-81..83 (all `[DECIDED]`: the assists-received detail, the streak history page and the tie-breaks are built, FR-WEB-9, FR-WEB-25).
- **Charts** (FR-WEB-16, 2026-10-03): server-rendered inline SVG bar charts, no JS: pure layout in `web/charts.py`
  (`build_bar_chart(ChartSpec)`: 1/2/5 ticks, k/M abbreviations, legend from 2 series), `{% bar_chart spec %}` with `role="img"`, title/desc,
  per-bar `<title>`, a "Show the numbers" table and an empty state; colours `--il2-chart-1/2` (steel blue, rust) checked for contrast in
  both themes. Home: sorties per day for the 30 days ending at the newest active day (level-2 `ActivityDay`, UTC days by mission start,
  hidden missions excluded and refreshed when an admin hides one). Profile: sorties per tour and air kills/deaths per tour (from
  `PlayerTour`, with ≥ 2 tours, latest 12). +1 query on each page. Choices: OQ-59.
- **Score and leaderboards** (FR-WEB-7/19/20, 2026-10-03, reworked 2026-10-04): pure `score_sortie(SortieFacts, ScoreRules)` in
  `core/ratings/score.py` gives an air and a ground score per pilot sortie (gunners 0), stored as `PlayerSortie.air_points/ground_points` and
  summed into the counter tables (`score_air`, `score_ground`, `score_ground_attack`). Rules (doc 13 "Score") and leaderboard minimums are the
  `[score]` config section; a rule change applies with `il2ks rebuild-aggregates` (scores read only stored columns; 6.6 s for 210 missions).
  The per-hour boards are read-time F-expressions of two stored counters. **Boards** (`queries.leaderboards.BOARDS`, in this order; OQ-84):
  `elo-jet`, `elo-prop`, `air` (air score), `interception` | `ground-hour`, `tank-busting`, `ground` (ground score). Routes: `/leaderboards/` (the
  air score board) and `/leaderboards/<board>/` with `?tour=` (current tour by default, `?tour=all`), `?aircraft=` (per-type rows; not on the Elo
  boards), `?pool=prop|jet` (from `PlayerPool` / `PlayerTourPool`; a chosen aircraft type overrides the pool), `?sort=`, `?page=`.
  The Elo boards are all time with no tour, aircraft or pool filter. **No kills board**: `/leaderboards/kills/` is a permanent redirect to the
  index. Hidden players never appear. **Switcher**: a `div role="navigation"` with a label above each group ("Air", "Ground") and one icon button
  per board (chess pieces for Elo, the role icons for the scores, `stat/interception`, `ground/tank`); a button keeps the tour, pool and aircraft
  choice only where the target board has that filter (`_tab_url`), and it works without JavaScript (the earlier `nav` element let the
  framework's nav rules overlap label and first button; a Playwright check covers four widths). The page note names the board's minimum
  (encounters, sorties, attack sorties and minutes on target, air superiority sorties and minutes). Links from the all-time home block carry
  `?tour=all` (TD-26). Profile block `players/detail_scores.html` (scores follow the selected tour; Elo stays all time, labelled). Values and
  product choices: OQ-62..64, OQ-67, OQ-84..86, OQ-102..104 (all `[DECIDED]`).
- **Aircraft stats** (FR-WEB-8, 2026-10-03; OQ-122: every section follows tour, role and mod filter, ⏳ to build, until then the page notes which parts do not; **per tour** since 2026-10-04, maintainer: `/aircraft/?tour=` and the detail page's tiles,
  pilot count and matchups follow one selector above the tiles, from the level-2 `TourAircraftStats` (tour, aircraft) next to the all-time
  `AircraftStats`, both on an abstract `AircraftCounters`; top pilots, hits to destroy, loadouts and the side badge stay all time and the
  page says so; OQ-114): `/aircraft/` lists flown types (prop/jet, side, sorties, pilots, flight time, kills, deaths,
  losses, K/D, K/L, survival, attack share, hits to destroy; sortable); `/aircraft/<pk>/` adds **matchups vs each enemy type** (below), **top pilots** (hidden
  players left out; by per-type Elo, and ground score per hour on target for attack work, under the leaderboard minimums; types with an attack
  share of 50% or more list the ground ranking first), hits to destroy per ammo, loadouts. Level-2 `AircraftStats` / `AircraftMatchup` /
  `AircraftPayload`, built by `ingest/aircraft_stats.py` (incremental == rebuild). **Matchups follow the tour selector** and a toggle "All fights /
  Intercept flights only" (`?tour=`, `?intercept=1`; `AircraftMatchup.tour` / `.intercept`, an intercept fight being two air superiority sorties),
  sortable by enemy, kills, losses, encounters and ratio; a matchup shows its exchange share and can be named best or worst from **10** fights
  (`MIN_ENCOUNTERS`; `[DECIDED]` maintainer, OQ-110). Top pilots, hits to destroy and loadouts are all time (the tiles follow the tour, OQ-114). **No ratio is stored** (OQ-98):
  the list sorts K/D, K/L, survival and attack share with `queries.sorting.Ratio`. Optional columns: see above. Rules: OQ-65.
- **Ammunition mixes and loadouts** (FR-WEB-18, 2026-10-04, OQ-116; FR-WEB-4): the aircraft detail's hits-to-destroy table has an **Instances**
  column (counted kills in which the ammunition hit at least once) and below it **Ammunition mixes**: the same single-attacker kills grouped by
  which gun ammo types hit together (`MissionAircraftAmmoMix` per mission, `AircraftAmmoMixStats` summed, all time) with the average hits of each
  type in the mix. The player profile's per-aircraft table gets an extra row per type with the **favourite loadout** (its share of the pilot's
  sorties) and a `<details>` with all loadouts, the weapon-modification sets and the gun ammo mix, all from `PlayerAircraftBuild` (one read;
  all time or the selected tour; the log has no belt field, so ammo is what hit, not what was picked). The sortie page's summary has a
  **Modifications** row (names from `weapon_mods.csv`, "Unknown modification (id k)" without a name, "None").
- **Stat highlights** (FR-WEB-22, 2026-10-03; marks for Elo and the scores 2026-10-04): level-2 `StatThreshold` rows (p10/p25/p50/p75/p90,
  linear interpolation) per metric, all-time and per tour, only when ≥ 20 pilots qualify. The population follows the board the figure sits next
  to: ≥ `[marks] min_sorties` (20) sorties for the ratios, air score and ground score; ≥ `min_elo_games` encounters in the pool for **Elo jet and
  Elo prop** (all time only); ≥ the boards' time on target for **ground score per hour** and **tanks per hour**; ≥ the boards' air superiority
  flight for **interception per hour** (the unit of that minimum is stored in `StatThreshold.min_sorties`, doc 13 "Stat marks");
  recomputed per saved mission (a few ms) and by rebuild-aggregates. Percentiles, not mean + 2σ: the ratios are skewed with a floor at 0
  and survival is capped at 100%. `{% stat_mark "key" %}` on the profile's ratio, score and rating lists: above p90 "Top 10%" (accent), above p75 "Top
  25%" (muted), strict `>`; low values are never marked. Real data (216 pilots with ≥ 20 sorties, September 2026): survival p50 74% /
  p90 87%; K/D p50 0.34 / p90 2.96; K/L 0.26 / 1.80; air kills per sortie 0.09 / 0.48, per hour 0.37 / 1.71; ground kills per sortie
  2.9 / 13.7. +1 query on the profile. Rules: OQ-66.
- **Tours on pages** (2026-10-03, current-tour default 2026-10-04): the profile (totals, tiles, ratios, ground kills, hall of shame,
  per-aircraft table and recent sorties follow the tour), the mission list, the player's sortie list, the killboard and the leaderboards have a
  tour selector and **open on the current tour** (the newest `Tour` row, data-only so caching and ETags stay valid) when `?tour` is absent
  or unknown; `?tour=all` is all time; ids over 18 digits count as unknown (stale links still answer 200). **Links from an all-time context carry
  `?tour=all`** (`queries.tours.tour_query`; TD-26): a bare link would silently mean the current tour. **One dropdown only, no segmented
  toggle: "All time" and "Current tour" are its top two entries** (`[DECIDED]` maintainer, 2026-10-04, OQ-78; being applied). The "Next tour
  starts <local time>" line sits beside the dropdown (hidden in manual mode, and by JS once past). A tour
  without sorties shows the `tour_empty` flavor text. Views use `queries.tours.tour_choice_from(request.GET)` (one query); templates use
  `{% tour_select %}` (swaps `#main`, works without JS) or `{% tour_filter %}` on the list pages (the same dropdown). Titles are localised at display time
  (`tour_title`: "Month YYYY" via `YEAR_MONTH_FORMAT`, "Tour N" via gettext; anything else is an admin rename, shown as is). Elo stays
  all-time. Choices: OQ-45..48, OQ-78..80.
  **Pages without a tour dropdown (`[PROPOSED]`, release audit 2026-10-04):** the player search (`/players/`) is a name search, so a tour would filter nothing useful; the Elo boards are all time (see above). Achievements follow the tour now (OQ-105: the profile, `/players/<pk>/achievements/`,
  `/achievements/` and `/achievements/<key>/`, plus the home page's "Recently earned" strip; doc 17). `/streaks/` follows the tour. A tour dropdown appears on a page
  only where its numbers exist per tour.
- **Local times** (FR-WEB-17, TD-15): `localtime.js` formats every `<time>` with `Intl.DateTimeFormat` (`dateStyle: medium`, `timeStyle:
  short`) in the page language and the browser's zone; the zone is named only in the footer; the UTC time stays in the tooltip.
- **Setup page** (`/setup/`, installer path): answers 404 unless a setup token file exists (`il2ks web` / `run` write it while no admin exists),
  the request is straight from this machine (loopback peer, no proxy headers, `localhost` Host) and the URL carries the token (constant-time
  check, guesses counted, then locked out). On submit it writes the config via `ops.setup.complete_web_setup`, creates the admin and deletes the
  token; from then on 404. The image disables it (`IL2KS_SETUP_PAGE=off`; the logs explain `il2ks createadmin` when no admin exists, documented in `docs/install-docker.md`; `[DECIDED]` maintainer, 2026-10-04, OQ-70). Code: `web/views/setup.py`, `serving/setup_token.py`.
- **Query budgets** (TD-22; a test per page, shared constants in `tests/simple_reads.py`; every number includes the 2 context-processor reads;
  never raise one without a reason in the test). Home **16** (15 with no missions; `HOME_READS`, `HOME_READS_EMPTY`: the 10 extras are the six compact
  boards, the tour list of the selector, the online-now snapshot and the "Recently earned" feed, 2 reads), mission list 5, mission detail 5, player search 4 (also with every optional column), profile **16** all
  time (`PROFILE_READS_ALL_TIME`, incl. the medals with their rarity and the favourite loadout `PlayerAircraftBuild`) and **17** for a tour, which includes the default current tour (`PROFILE_READS_TOUR`: + the `PlayerTour` row),
  player sortie list 7 (with or without optional columns; the column and fate tests allow 8), sortie detail **9** (+ the earned medals and their rarity), killboard 8, best streaks 5, streak history 6, streak list 8 (best + running, each with its count; 6 on a past tour, which has no running list),
  leaderboards 6 (7 with tour + pool; the Elo boards 4), aircraft list 5 (+ the tours of the selector), aircraft detail **11** (9 + the tour's tiles + the ammo mixes), achievements: a player's list 6, the overview 4, a
  holders page 6, live fragment 3 to 5. The `tests/perf/` suite (doc 08) has its own, looser per-page limits over a larger seeded world (N+1 guard);
  `uv run il2ks dev check --full` runs it, and CI runs it as the separate job `test-perf` (SQLite, then Postgres; OQ-97). On Postgres the perf
  database gets `ANALYZE` after seeding: without it stale planner statistics made the mission page take 59 s.
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
`:root` (light and dark via `light-dark()`); a unit test forbids colors elsewhere and fails when a `:root` color has no entry in `web.theme.TOKENS`, so every color is editable in the admin (the medal tiers and the shadow color included; the camouflage image is not a color; derived values such as hover mixes follow their base token). `SiteSettings.theme` stores only overrides per mode;
`il2ks.web.theme.theme_css` emits them from validated `#RRGGBB` values and fixed font stacks (no CSS injection). The admin warns (never
blocks) on WCAG contrast below 4.5:1 (charts 3:1). Presets: Steel blue, Desert sand, High contrast, Default. Fonts: chosen by key from bundled
and system stacks or from the admin's **uploaded fonts**, no third-party font host. **Custom fonts** (2026-10-04, `web/fonts.py`): the
admin uploads a `.woff2` (preferred) or `.woff` file (at most 2 MB, up to 6 kept, a non-blocking warning above 150 KB); like the logo the
upload is untrusted: only the two extensions, the magic number (`wOF2` / `wOFF`) must match the extension, the header's length field must equal
the real size (a truncated or padded file fails) and the sfnt flavour must be known. The stored name and the CSS family are built from the
content hash (`branding/font-<hash16>.woff2`, `il2-font-<hash>`; nothing the admin typed reaches the CSS, the label from the file name is only
shown, escaped), served like the logo by the media view (`nosniff`, strict CSP, a year of immutable caching). The theme CSS emits an
`@font-face` (`font-display: swap`) per uploaded font and a font is selected by its key `up-<hash8>` in the heading or body choice
(`SiteSettings.custom_fonts`, readers re-validate). Guide for admins: `docs/customizing.md`. Every branding save bumps the data version (TD-28). Product
choices (`[DECIDED]`, maintainer, 2026-10-04): navigation link URLs up to 2000 characters (detail pages), new tab, 3 recommended (OQ-87); **Default** is the original military theme, a theme can be built from scratch (every token editable), the contrast check stays a simple warning after save (OQ-88).

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

## Accessibility `[PROPOSED]`

Target: WCAG 2.1 AA for every public page, in light and dark, at phone and desktop width. `tests/e2e/test_accessibility.py` runs
axe-core (tags wcag2a/2aa/21a/21aa + best-practice; the bundled `axe.min.js` of the dev-only `axe-playwright-python`, injected by the
test, never shipped, no CDN) on every page of `test_visual_qa.PAGES` (menus opened too) and on home/leaderboards/mission in each language
(also checks `<html lang>`); its waiver list `ALLOWED` is empty. `tests/e2e/test_keyboard.py` drives the column picker, tour select,
board switcher and pagination with the keyboard only. Conventions the tests protect: icons are `aria-hidden` and always sit beside text
or inside a control with an `aria-label`; several paginations on one page get distinct names (`{% pagination ... label=%}`); a scrolling
`.table-wrap` gets `tabindex="0"` from `il2ks.js` (only while it overflows) so its columns are reachable by keyboard. The first run found
only two rules (scrollable-region-focusable on narrow tables, landmark-unique for the second pagination on a page); contrast, labels and
`lang` were already clean. Not covered by axe (needs a person): reading order with a screen reader, zoom to 200 %, the admin pages.

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
  writer holds the lock (`il2ks run`) or the web port answers (exit code 3; `--force` overrides) and when a newer il2ks made the backup, takes a
  safety backup, swaps in the database, config, `custom/`, `media/`, server ID and secret key, verifies. SQLite only. `[DECIDED]` (maintainer,
  2026-10-04, OQ-71: restore refuses while the site runs; scripts need `--force`).
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
