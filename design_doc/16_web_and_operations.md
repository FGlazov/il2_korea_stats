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
  `damage_taken`. A gunner sortie shows its turret type as plain text (no link to an aircraft page: a turret type has none, `[PROPOSED]`). The matchup K/L hint says "no losses" where the type was never lost to the enemy and shows a dash below 10 kills plus losses (`MIN_ENCOUNTERS`). Medal hover texts build the tier and threshold from one translatable pattern.
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
  interception, attack proficiency, tank busting and play time** (`[DECIDED]` maintainer, OQ-64, OQ-104; the Elo boards follow the tour like the rest since 2026-10-05, OQ-128: the tour's Elo, all time the best tour's; titles and player names link with the page's scope, `?tour=<id>` or `?tour=all`), a streaks block of 5 (the longest streaks
  inside the tour) and the activity chart (the tour's own days). Elo games are called **encounters** in the UI (maintainer, 2026-10-04).
- **Pagination** `[DECIDED]` (maintainer, 2026-10-04, OQ-96: "100% paginate"; `queries/paging.py`): the mission list shows **10 missions** a page,
  every other long list **20 rows** (a player's sorties, players, leaderboards, killboard, streaks, achievement holders). The mission page paginates
  each coalition's sorties and the kills separately (`page_redfor`, `page_blufor`, ...), the sortie page its damage rows
  (`?page_damage=`); links keep every other parameter. **Not paginated, by design** (short or bounded lists): the aircraft list (one row per flown type) and the matchup table on the aircraft page (its ammo-mix, loadout and modification tables page since 2026-10-05, see below), the by-aircraft killboard tables (at most 60 enemy types), a player's best streaks and achievements pages and the achievements overview, the profile's fixed top-5 and latest-5 blocks, the sortie page's other tables, and the home page's blocks. **Exception** (maintainer, 2026-10-04): the sortie page's timeline is not paginated, every row is shown (a detail page, so it gets a higher server-time and query budget; the HTML stays lean because icons are a sprite). Real-log mission and sortie pages fell from 107-122 KB of HTML to
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
  OQ-38. **Role toggle** `[DECIDED]` (maintainer 2026-10-05; like the aircraft pages' `?role=`, All / Air superiority / Attack, under the tour selector, only for a pilot with sorties in the scope): the tiles, ratios, ground figures, PvE, "Other totals", the aircraft table and the latest sorties count only that role's sorties (`PlayerRole` and `PlayerAircraftScope` rows, no query added: a role view skips the medals, the attack Elo and the charts). No per-role data, so left out in a role view: medals and hall-of-shame ribbons, the favourite loadouts, the per-tour charts, Elo and interception per hour for attack; unchanged with a note: the killboards ("count every role"); the ironman block is not role-scoped. A pilot without sorties in that role gets a notice and the toggle. **Order**: the part of the role comes first (attack: air-to-ground first; the in-page nav follows), under All the pilot's main role (at least half attack sorties = air-to-ground first). **"Other totals"** is a grid of label-over-value cells, 4 columns from 760 px, 2 on a phone. Partials: `players/detail_*.html`, listed at the top of `players/detail.html`; scores and Elo follow the tour (all time: the best tour's Elo, labelled).
- **Sortie page** (FR-WEB-6, extended 2026-10-04): the pilot fate as Dead / Captured / Survived with the stored fate as a note (`pilot_fate_badge
  **Rams** (2026-10-05, maintainer, `[DECIDED, PRODUCT]`): a sortie with a ram (`ram` on its stored timeline rows, from `Kill.is_ram`) shows an orange **Ram** badge (`event/ram` icon) next to the outcome badge, tooltip "Collided with <enemy>: counts as a kill for both pilots. Ram detection can occasionally be wrong."; the kill row and the shot-down row each carry a Ram badge, and a muted line under the timeline says rams are detected from the log (0.5 s / 15 m) and may occasionally be wrong. None of it on sorties without a ram. The ram quip spot (FR-WEB-23) is unchanged.
  sortie detail=True`); the timeline table has a **Damage** column with the signed percent of each hit row (+ given, − taken, from the `hit_given` /
  `hit_taken` rows, doc 13 "Timeline hits") and the ammo of the nearest hit; the ammo table dashes "Left" after a loss and explains why (doc 13);
  "Earned in this sortie" lists the medals (+1 query); the quip (below). Timeline **hit rows are tinted**: light green for a hit given, light red for a
  hit taken (a 9% mix of the `--il2-green` / `--il2-red` theme tokens in `sorties.css`, so both themes work). Air and ground assists are listed apart.
- **Aircraft list columns** (maintainer, 2026-10-05): the default columns are Aircraft, Sorties, **Elo** (the aircraft type's own rating, doc 13 "Aircraft type
  Elo"; sortable, a dash while the type has no rated duel, tooltip "rating of the aircraft type from air-superiority duels between types"), K/L, Survival and
  **Attack proficiency** (ground score per hour on target; the `ground_hour` sort key). Every other column the list had (pilots, flight time, air and ground kills,
  deaths, aircraft lost, K/D, attack sorties, hits to destroy, the earlier extras) is an optional column; "Hits to destroy" is the one extra that does not sort
  (`Column.sortable`: its value lives in the ammo tables, not on the row). There is no separate "Aircraft" board: the sortable Elo column is the view. The
  aircraft page shows the type's Elo as one tile. Query budget unchanged (5 + the tours of the selector).
- **Optional columns** (FR-WEB-27, 2026-10-04): the player search, mission list, aircraft list and a player's sortie list keep their default
  columns in their templates and offer more through a collapsible **"Extra columns"** (`components/columns_picker.html`: every choice a checkbox in a compact grid, open when a column is ticked, a plain GET form that works without JS; with htmx the swap keeps the open state, the focused checkbox and each table's horizontal scroll, `il2ks.js`). The first column of every `.data-table` stays pinned when the table scrolls sideways (opaque row-matching background; `data-table--sticky-2` pins a leading rank/time column and the name next to it).
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
  one by SHA-256 of `spot:seed` over the spot's effective list (stable across restarts, so caching holds; **not across languages** once an admin limits a custom quip to one language: the pool, and so the pick, then differs per language; the page cache varies by language anyway `[PROPOSED]`); `{% sortie_flavor %}` walks the spots of `flavor.sortie_spots(sortie, highlights)` in order and takes the first spot that **has a line**: a spot the admin switched off, or whose pool is empty in the page's language, **falls through to the next matching spot** (so a first-blood sortie with three kills whose first-blood spot is off still gets its ace line, `[PROPOSED]`). The order of matches is: taxi accident, friendly kills, captured, shot down
  by an AI gunner, ditched, **strafed** (`sortie_strafed` parked, `sortie_strafed_landed` after a landing; doc 13 `[DECIDED]`, OQ-112), shot down by AA, **bomber hunter** (2+ air kills of bomber, attacker or transport class; `BOMBER_KILLS_MIN`), **first blood** (the sortie made the mission's first credited PvP air kill, `PlayerSortie.first_blood`, doc 17), **ace** (3+
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
  **Admin-configurable** (roadmap "Admin-configurable quips", 2026-10-04; `web/quips.py`, `web/admin_quips.py`, `SiteSettings.quips_enabled` /
  `.quips`): a global switch (on by default) and per spot a mode: `defaults`, `defaults_and_custom`, `custom_only` or `off`; single built-in lines can be
  hidden (by their English text, so a reworded line is an "orphan" the admin page lists and can forget); custom quips are plain text (escaped
  by the templates, at most 200 characters, 20 per spot, 300 per site), each for one language or every language, and can be disabled. A custom
  quip with a language matches that language or its base (`pt` for `pt-br`). Nothing is saved when anything in the form is invalid. All
  `[PROPOSED]` (limits and fall-through are agent choices).
- **Game rules in the admin** (FR-ADM-7, maintainer decision 2026-10-05): the game-rule settings of `il2ks.toml` are editable in four pages: **Scoring** `/admin/score/` (`admin_site.score_view`: `[score]` points and penalties, `[ratings]`, `[killboard] assists`, plus the optional flight-time score: a checkbox, off by default, and points per hour, default 1, 0..100), **Tours** `/admin/tours/` (`[tours]` mode, start, time zone, plus the decisive-mission switch below), **Rules** `/admin/rules/` (`[rules]`, `[replay]`) and **Leaderboards** `/admin/leaderboards/` (the board minimums, `[marks] min_sorties`). One shared row template (`admin/_rule_fields.html`): label, input (a select for yes/no), the file's value as the placeholder, a "pending" mark and one line on how the field applies. Machine settings stay in the file (classification: docs/settings.md).
  - **Storage and precedence**: `SiteSettings.rule_settings` (what the admin chose) and `.rule_settings_applied` (what the stored numbers were computed with), flat JSON `{"score.air_kill_pvp": 12.0, ...}` (migration 0092). The effective rules are the file's (or the defaults) overlaid with the **applied** values: `il2ks.rule_settings.overlay` reads them with `config.load_rule_set`, the same parser that reads the file, so the two cannot disagree on what is valid (`validate` reports each wrong field, then the combination, e.g. `days:N` needs a start). Blank = no override. Every path that builds rule objects calls `ingest.rule_store.effective_config(cfg)` (pipeline `save`/`replay` per mission, `ingest`, `reprocess`, `rebuild-aggregates`, live, backfills, doctor); the boards read `queries.leaderboards.rules(site_row)` and the Tours admin list reads the same. `[live]` is not editable: `watch` reads it at start.
  - **Effects** (`RuleField.effect`): `rescore` (scoring, ratings, assists, and `min_elo_games`, which re-scores nothing but is recomputed by the rebuild: the Leaderboards page shows it with its own effect text) and `retour` (tour mode, start, time zone) write the wanted side only and show "pending" until `ingest.score_apply.rescore_with_wanted` (`watch`, every tick, writer lock) ran **one** rebuild for everything pending (the flight-time option and the decisive-mission switch included; `retour` first when a tour setting changed) in one transaction together with adopting the applied values and `bump_data_version`; a crash rolls back and leaves it pending. `rebuild-aggregates` adopts every wanted value except the tour settings, which need `--retour`. `display` (the board minimums except `min_elo_games`, marks) is written to both sides at once and, when a minimum feeds the marks, the stored thresholds are recomputed in the same transaction. `reprocess` (`[rules]`, `[replay]`) is written to both at once: new missions use it, older ones need a reprocess (the page says so). Every path reads the rules through `effective_config` at the moment it uses them (the live discard, a long run's thresholds at its end); the settings row is locked (`select_for_update`) in `save_overrides` and in the adopt step, which keeps the `display` and `reprocess` keys of the current row, so a save during a rebuild is never undone. The shared parser rejects `nan`/`inf`, whole numbers above 2 000 000 000 and `days:N` above 3660, with a `ConfigError` (a translatable template for the admin, `ConfigError.localized()`).
  - Permission `change_sitesettings`, CSRF as the other admin pages; nothing is saved when any field is invalid, and the form comes back as typed. `il2ks doctor` shows how many rules the admin set (they win over the file) and warns while a change is pending. Rule and defaults: doc 13 "Score". `[PROPOSED]` (page split, effects per field, `[live]` and `[server] timezone` staying in the file).
- **Sortie map** (FR-WEB-12, 2026-10-03; **not on main**: benched until after the release, OQ-54/55, the code stays on its branch): an accordion (open) on the sortie page with a server-rendered inline SVG of the key events
  from the stored timeline (+0 queries): numbered markers with the event icons, a faint dashed line in time order (labelled "not the
  flight path"), a km grid in absolute game coordinates, an N arrow, a legend, `<title>` tooltips; the timeline table is the textual
  equivalent. Axes: game `x` = north, `z` = east (IL-2 convention; x checked against airfield positions in the samples, z assumed).
  Pure geometry in `web/map_geometry.py`, view model in `web/sortie_map.py`. **Map images drop in** as
  `static/il2ks/img/maps/<map-id>.webp|png|jpg|svg` with bounds in `maps.json` (overridable in `custom/static`); placeholder bounds for
  `korea` are 0–512 km. Until a mission records its map, the id is `korea`. Events with missing, origin or off-map positions are skipped
  and counted. Which events are drawn: OQ-54.
- **Player list columns** (maintainer 2026-10-05): after the name the list shows flight time, **Elo** (the higher of the two pools' ratings among the rated ones, a dash without a rated game; the pools stay optional columns), attack proficiency, K/L, the **longest kill streak** (best air ironman run by air kills) and the **longest ground kill streak** (best ground run by ground kills): the last two are `Player.streak_kills_air` / `streak_kills_ground`, copied by `rollup_streaks` so the list needs no join (budget unchanged: context 2, count, rows). Sorties, air and ground kills, deaths and last seen moved to the optional columns; the default sort is still last seen. All are sortable (`PLAYER_SORTS`).
- **Killboard and ironman streaks** (FR-WEB-9 / FR-WEB-25, built 2026-10-03, extended 2026-10-04): level-2 `PlayerKillboard` (two mirror rows
  per pair: kills, deaths, assists, last encounter) and `PlayerTourKillboard` (the same per tour), `PlayerStreak` (current and best streak:
  sorties, air kills, flight time) and `PlayerBestStreak` (best streak by sorties survived, air kills and flight time, all time and per
  tour; **a streak never crosses a tour (clean slate, 2026-10-05): the rule runs per tour and the all-time rows are rolled up from the tour rows** `[PROPOSED]`: best = max over the tours' bests, runs = union of the tours' runs, `PlayerStreak.current_*` = the run in the newest tour (`current_tour`; zero for a pilot who has not flown in it)), rebuilt per affected player in `recompute_players` (incremental == rebuild, checked
  on 45 sample missions). Pure streak rule in `core/streaks.py`. Pages: profile sections (Ironman; **Killboard by aircraft** and Killboard, top 5 each way),
  `/players/<pk>/killboard/` (`?tour=`, `?sort=`; the by-aircraft tables of up to 60 enemy types sit above the player table), `/players/<pk>/streaks/` (the player's best streaks, a sub-page, `?tour=`), `/streaks/` (**since 2026-10-05 a permanent redirect to the ironman board**, maintainer: "Move longest iron man streak to leaderboards"; the boards: `/leaderboards/ironman-all/` (Ironman, every sortie), `/leaderboards/ironman-air/` and `/leaderboards/ironman-ground/`, one per ironman track (doc 13), in an Ironman tab group of their own, **fixed columns** pilot, sorties in a row, air kills, ground kills (maintainer 2026-10-05; no Extra columns control), tour-aware like the other boards, the running streaks of the track below the table with the same columns; the old page, as built 2026-10-04:
  tour-aware, TD-26: a tour dropdown; every visible pilot's best streak by sorties in the selected tour or all time from `PlayerBestStreak`, 20 per page as `page_best`, each row linking to `/players/<pk>/streaks/history/` with the same tour; below it the running streaks from `PlayerStreak`, `page_running`, shown on the current-tour and all-time views and hidden on a past tour because nothing is running in a finished tour `[PROPOSED]`), a home block of 5 whose "All streaks" button carries the block's tour (`?tour=`). The **killboard by aircraft type** (2026-10-04) is the level-2 `PlayerTypeKillboard` (per player, enemy
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
  The Elo boards have the tour filter (the tour's final Elo from `PlayerTourPool`, OQ-128; all time the best tour's, from `Player`) and no aircraft or pool filter (their pools are the split; a `BoardRow` carries `rating` / `games` whichever table it comes from). **No kills board**: `/leaderboards/kills/` is a permanent redirect to the
  index. Hidden players never appear. **Switcher**: a `div role="navigation"` with a label above each group ("Air", "Ground") and one icon button
  per board (chess pieces for Elo, the role icons for the scores, `stat/interception`, `ground/tank`); a button keeps the tour, pool and aircraft
  choice only where the target board has that filter (`_tab_url`), and it works without JavaScript (the earlier `nav` element let the
  framework's nav rules overlap label and first button; a Playwright check covers four widths). The page note names the board's minimum
  (encounters, sorties, attack sorties and minutes on target, air superiority sorties and minutes). Links from the all-time home block carry
  `?tour=all` (TD-26). Profile block `players/detail_scores.html` (scores and Elo follow the selected tour; all-time Elo is the best tour's and labelled so). Values and
  product choices: OQ-62..64, OQ-67, OQ-84..86, OQ-102..104 (all `[DECIDED]`).
- **Aircraft stats** (FR-WEB-8, 2026-10-03; per tour since 2026-10-04, maintainer, OQ-114; **every section follows tour, role and modification
  filter**, OQ-122, built 2026-10-04 on the scoped level-2 rows of migration 0057). `/aircraft/` lists the flown types of the selected tour
  (`?tour=`, current tour by default, `?tour=all`) and, with `?role=air_superiority|attack`, only the sorties of that combat role: prop/jet, side,
  sorties, pilots, flight time, kills, deaths, losses, K/D, K/L, survival, attack share, hits to destroy (always all time, always every role);
  sortable, not paginated. `/aircraft/<pk>/` (404 for a type nobody flew) has three independent selectors above the tiles `[PROPOSED]`: the
  **tour** (as everywhere), the **role** (`?role=`, default every role; the Elo table is hidden for `attack`, the ground table for
  `air_superiority`) and, for a type with significant weapon modifications (`weapon_mods.csv`, `significant`), a with / without / any switch per
  modification (`?mod<id>=with|without`). **Scope rules `[PROPOSED]`** (the stored rows carry every scope, so each section is still one simple
  read, TD-22):
  - the **tiles and pilot count**: `TourAircraftStats` (tour, role, mod pattern), or `AircraftStats` for all time, every role, no filter; the
    side badge is the type's all-time majority side (`AircraftStats.side`), not the scope's;
  - **loadouts** and the **weapon-modification sets** tables: `AircraftPayload` / `AircraftMods` of the tour, role and filter, each with the
    effectiveness measures (average pilot Elo `elo_avg`, air kills per sortie and K/D for air superiority, ground score per hour on target for
    attack, each only above the leaderboard minimums of the row's role, a dash otherwise), sortable apart from the matchups (`?lsort=`,
    `?msort=`; a dash sorts last either way). **Maintainer view pass 2026-10-05** `[DECIDED]`: a loadout, a modification set and an
    ammunition mix is listed from **10** sorties (mixes: 10 kills) on (`queries.paging.MIN_EVENTS_LISTED`; the totals still count every one) and
    the three tables page by 20 (`?page_loadouts=`, `?page_mods=`, `?page_mixes=`; htmx swaps `#main`). Under the role *all* the loadouts
    have a tab **Air superiority | Attack** (`?lrole=`, default the role most of the type's sorties are flown in) that shows that role's
    loadouts only, with the columns that fit it (air: air kills, deaths, average Elo, air kills per sortie, K/D; attack: ground kills,
    deaths, attack proficiency); with a single role on the page the table follows it and has no tab. Loadouts of sorties without a combat
    role (gunners) are therefore not listed under *all*. One short help line per mode replaces the long text;
  - **matchups** vs each enemy type (`AircraftMatchup`): the role and the modifications scope **this type's own sortie** (the killer's for its
    kills, the victim's for its losses; `scoped_side`), the enemy is unrestricted. The **intercept** toggle "All fights / Intercept flights only"
    (`?intercept=1`, an intercept fight = two air superiority sorties) is independent of the role and combines with it, except that the **attack role has no toggle** (an intercept fight is two air superiority sorties, so the table would always be empty; a stale `?intercept=1` is ignored). Sortable by enemy,
    kills, losses, encounters and ratio; a matchup shows its exchange share and can be named best or worst from **10** fights (`MIN_ENCOUNTERS`;
    `[DECIDED]` maintainer, OQ-110);
  - **hits to destroy**: one table of **ammunition mixes** (below; a single ammunition is a mix of one): the kills OF this type, so the tour, role and modifications are those of
    the **destroyed aircraft's own sortie** (`MissionAircraftAmmo(Mix).combat_role` / `.weapon_mods`); an AI aircraft (`NO_MODS_RECORDED`, no
    role) counts for the every-role, unfiltered scope only;
  - **top pilots** (hidden players left out): *by per-type Elo*, which follows the tour (`PlayerTourAircraft.elo`; all time: `PlayerAircraft.elo`, the best tour's, OQ-128); in a narrower scope a pilot must in addition have flown at least the leaderboard minimum of air superiority sorties within it
    (`PlayerAircraftScope`), and the sorties shown are the scope's; *by ground score per hour on target* from the scope's own rows, under the
    ground-per-hour board's minimums. Types with an attack share of 50% or more list the ground ranking first (always, for `role=attack`).
  Level-2 `AircraftStats`, `TourAircraftStats`, `AircraftMatchup`, `AircraftPayload`, `AircraftMods`, `AircraftAmmoStats`,
  `AircraftAmmoMixStats`, `PlayerAircraftScope`, built by `ingest/aircraft_stats.py` (incremental == rebuild). **No ratio is stored** (OQ-98): the
  list sorts K/D, K/L, survival and attack share with `queries.sorting.Ratio`. Optional columns: see above. Rules: OQ-65.
- **Ammunition mixes and loadouts** (FR-WEB-18, 2026-10-04, OQ-116; FR-WEB-4): the aircraft detail's hits-to-destroy section is **one table** (maintainer
  2026-10-05, replacing the per-ammo table plus the mixes table): a summary row (all gun ammunition: counted kills, average hits), then one row per
  ammo mix (the single-attacker kills grouped by which gun ammo types hit together, `MissionAircraftAmmoMix` per mission,
  `AircraftAmmoMixStats` summed per scope; one ammunition is a mix of one), most kills first, with the kills and the **average hits of each type in
  the stored order of the mix**, `5.4 + 2.7` (one decimal). Only mixes with 10 kills are listed and they page by 20; the per-ammo
  `AircraftAmmoStats` rows stay (the aircraft list and the summary row read them). The player profile's per-aircraft table gets an extra row per type with
  the **favourite loadout** (its share of the pilot's sorties) (name and share only; no `<details>`), from `PlayerAircraftBuild` (one
  read; all time or the selected tour; **loadout only** since OQ-117: the weapon-modification sets and the ammo mix are gone from the profile and
  live on the aircraft page, where they follow the scope). The sortie page's summary has a **Modifications** row (names from `weapon_mods.csv`, "Unknown modification
  (id k)" without a name, "None").
- **Stat highlights** (FR-WEB-22, 2026-10-03; marks for Elo and the scores 2026-10-04): level-2 `StatThreshold` rows (p10/p25/p50/p75/p90,
  linear interpolation) per metric, all-time and per tour, only when ≥ 20 pilots qualify. The population follows the board the figure sits next
  to: ≥ `[marks] min_sorties` (20) sorties for the ratios, air score and ground score; ≥ `min_elo_games` encounters in the pool for **Elo jet and
  Elo prop** (all time: the best-tour ratings; per tour: that tour's, from `PlayerTourPool`, OQ-128); ≥ the boards' time on target for **ground score per hour** and **tanks per hour**; ≥ the boards' air superiority
  flight for **interception per hour** (the unit of that minimum is stored in `StatThreshold.min_sorties`, doc 13 "Stat marks");
  recomputed per saved mission (a few ms) and by rebuild-aggregates. Percentiles, not mean + 2σ: the ratios are skewed with a floor at 0
  and survival is capped at 100%. `{% stat_mark "key" %}` on the profile's ratio, score and rating lists: above p99 "Top 1%" (solid accent fill), p95 "Top 5%" (stronger tint), p90 "Top 10%" (tint), p75 "Top
  25%" (muted), strict `>` (the two upper tiers 2026-10-05; the styles are in `site.css`, shared with the sortie page); low values are never marked. Real data (216 pilots with ≥ 20 sorties, September 2026): survival p50 74% /
  p90 87%; K/D p50 0.34 / p90 2.96; K/L 0.26 / 1.80; air kills per sortie 0.09 / 0.48, per hour 0.37 / 1.71; ground kills per sortie
  2.9 / 13.7. +1 query on the profile. Rules: OQ-66.
  **Sortie page** (2026-10-05): `{% sortie_mark sortie_marks "air_kills" %}` after the air-kills and ground-kills tiles; `queries.stat_marks.sortie_thresholds`
  (one SELECT of the sortie's tour and all-time `SortieThreshold` rows, only for a pilot sortie with kills, so the detail budget is 2 + 8 instead of 2 + 7)
  and `sortie_view.sortie_marks` (pure: best tier, value 0 never). **Damage section** (2026-10-05): the dealt / taken breakdown is a collapsed
  `<details id="damage">` below the timeline (summary "Damage" and the row count; native, keyboard accessible); it starts open when `?page_damage=` is asked
  for (the pagination links), and `sortie.js` opens it for `#damage` (load and `hashchange`).
- **Tours on pages** (2026-10-03, current-tour default 2026-10-04): the profile (totals, tiles, ratios, ground kills, hall of shame,
  per-aircraft table and recent sorties follow the tour), the mission list, the player's sortie list, the killboard and the leaderboards have a
  tour selector and **open on the current tour** (the newest `Tour` row, data-only so caching and ETags stay valid) when `?tour` is absent
  or unknown; `?tour=all` is all time; ids over 18 digits count as unknown (stale links still answer 200). **Links from an all-time context carry
  `?tour=all`** (`queries.tours.tour_query`; TD-26): a bare link would silently mean the current tour. **One dropdown only, no segmented
  toggle: "All time" and "Current tour" are its top two entries** (`[DECIDED]` maintainer, 2026-10-04, OQ-78; being applied). The "Next tour
  starts <local time>" line sits beside the dropdown (hidden in manual mode, and by JS once past). A tour
  without sorties shows the `tour_empty` flavor text. Views use `queries.tours.tour_choice_from(request.GET)` (one query); templates use
  `{% tour_select %}` (swaps `#main`, works without JS) or `{% tour_filter %}` on the list pages (the same dropdown). Titles are localised at display time
  (`tour_title`: "Month YYYY" via `YEAR_MONTH_FORMAT`, "Tour N" via gettext; anything else is an admin rename, shown as is). Elo follows
  the tour (OQ-128). Choices: OQ-45..48, OQ-78..80.
  **Pages without a tour dropdown (`[PROPOSED]`, release audit 2026-10-04):** the player search (`/players/`) is a name search, so a tour would filter nothing useful; the Elo boards have it (see above). Achievements follow the tour now (OQ-105: the profile, `/players/<pk>/achievements/`,
  `/achievements/` and `/achievements/<key>/`, plus the home page's "Recently earned" strip; doc 17). `/streaks/` follows the tour. A tour dropdown appears on a page
  only where its numbers exist per tour.
  **A pilot or aircraft type absent from the selected tour** (a new tour is a clean slate; maintainer 2026-10-05: "make sure your views still work when switching tours and suddenly the player is missing"; `[PROPOSED]`: the wording, the links and the extra read): every page answers 200 with the tour selector, and the profile, the player sub-pages (sorties, killboard, best streaks, streak history, achievements) and the aircraft page show `{% tour_absent %}`: "<name> did not fly in <tour>." / "<aircraft> was not flown in <tour>." with "Switch to:" All time and the tours the subject has rows in (`queries.tours.TourAbsence`; one extra read, only on an empty page; the profile reuses its tour-history read). Links to pilots and aircraft carry the tour of the page they sit on (`?tour=<id>`, `?tour=all` from all time; `tour_qs` filter); pages about a mission or sortie link their pilots with that mission's tour. Left bare on purpose: the player search and the online-now list (no tour context; they open the current tour and the notice offers the way out). Tests: `tests/integration/test_tour_robustness.py` (three tours, a pilot or type absent from each), `tests/e2e/test_flows_tours.py`.
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
  player sortie list 7 (with or without optional columns; the column and fate tests allow 8), sortie detail **9** (+ the earned medals and their rarity), killboard 8, best streaks 5 (6 when empty: the absence notice reads the pilot's tours), streak history 6, ironman boards 8 (best + running, each with its count; 6 on a past tour, which has no running list),
  leaderboards 6 (7 with tour + pool; the Elo boards 4), aircraft list 5 (+ the tours of the selector), aircraft detail **12** (11 + the scoped tiles + the mods table, `AIRCRAFT_DETAIL_READS`; one more for a type nobody flew in the tour: the absence notice), achievements: a player's list 6, the overview 4, a
  holders page 6, live fragment 3 to 5. The `tests/perf/` suite (doc 08) has its own, looser per-page limits over a larger seeded world (N+1 guard);
  `uv run il2ks dev check --full` runs it, and CI runs it as the separate job `test-perf` (SQLite, then Postgres; OQ-97). On Postgres the perf
  database gets `ANALYZE` after seeding: without it stale planner statistics made the mission page take 59 s.
- **Query-plan test and index allow-list** `[PROPOSED]` (maintainer request 2026-10-04: read-heavy, an index is cheap): `tests/perf/test_query_plans.py` runs EXPLAIN on every statement of every public page variant (every `?sort=` key both ways, filters, scopes, aircraft roles, loadout and mod sorts) and of one ingest pass over the seeded world, on SQLite (`EXPLAIN QUERY PLAN`) and on Postgres (`EXPLAIN (FORMAT JSON)` with seq scans and sorts made expensive, because on a few thousand seeded rows Postgres would always pick a seq scan). A full scan of a table that grows with play, or a sorted LIMIT of one, fails unless allowed. Tables and queries are discovered (from the plans and the models), so a new table is checked without editing the test; a coverage test fails for a table no plan reads until a page variant reaches it or `NOT_PLANNED_TABLES` gives a reason. **Allow-list (by design, no index):** leaderboard ties stay sorted alphabetically by name (the name lives in the joined `player` table, so one scope is sorted); player-list sorts other than last seen / name / air kills; the per-hour boards' ratio sorts; the player-name substring search; the ingest's whole-population passes (Elo replay and write-back, stat thresholds, OR-ed kill recomputes). Every entry carries a reason; details in `docs/performance-testing.md`.
- Coalition emblems: `{% coalition_badge %}` uses the site-settings choice (neutral by default, or placeholder insignia drawn as plain
  shapes: VVS, PLAAF, KPAF, USAF, ROKAF, UN).

## Admin (FR-ADM-1..5)

- The admin lives in the `web` app (`web/admin.py`): models stay in `db/`. Rows that ingest owns (players, missions, sorties, counters,
  runs) are read-only apart from `is_hidden`; nothing ingested can be added or deleted there.
- **Site settings** singleton (branding below; REDFOR / BLUFOR names and emblems; logo upload, FR-ADM-2 has the upload rules as built).
- **Hiding** (FR-ADM-3, `[DECIDED]`): bulk hide/unhide actions; presentation only. `Player.objects.visible()` / `Mission.objects.visible()`.
- **Names** (FR-ADM-5): game object and country display names editable inline, with "reset to catalog default".
- **Ingestion status** page `/admin/ingestion/` and a read-only run list (FR-ADM-4 as built).
- **Tour options** page `/admin/tours/` (`admin_site.tours_view`, `admin/il2ks_tours.html`; maintainer request 2026-10-05, TD-26 "New tour after a decisive
  mission"): one checkbox "Start a new tour when a mission is won by one side", **off by default**, plus how many missions have a result (won, draws, none).
  Saving stores `SiteSettings.tour_on_win` and bumps the data version; the page says "a reassignment of the tours is pending" until `watch` (every tick,
  `score_apply.rescore_with_wanted`, one rebuild with any other pending rule change) or `il2ks rebuild-aggregates --retour` applied it (`tour_on_win_applied`). The mission list and page show
  "Draw" when a mission's objectives were reported with no sole winner (`winner_badge`, `Mission.result`); the winner filter is unchanged. `[PROPOSED]`
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
- **`il2ks web`**: granian (WSGI) on `[web] host:port`; migrates first (with the pre-migration backup), then `collectstatic`. A changed catalog (`catalog:` fingerprint, `CATALOG_FILES`, doc 14) is refreshed on this start like a migration (backed up first, once; if an `ingest` holds the writer lock the web start goes on and the writer refreshes).
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
