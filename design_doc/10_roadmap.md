# 10 — Roadmap

Status: `[PROPOSED]` (ordering) with `[DECIDED]` items marked in the requirement docs. Iterations are ordered by dependency, not by date.

## Iteration 0: Foundations and log discovery
- ✅ Get real IL-2 Korea DServer logs: 210 missions in `sample_data/` (gitignored), 2026-10-02.
- ✅ First version of [12_korea_log_format.md](12_korea_log_format.md).
- ✅ Validated a bailout rule against `sample_data/` (rule v2, FR-ING-14). Accepted for now and to be iterated later. The maintainer will compare notes with the other developer.
- ✅ Repo skeleton (2026-10-02): `pyproject.toml` (uv, Python 3.13, Django 5.2, granian, WhiteNoise), Django project with the first model
  (`GameObject`) and migration, `il2ks` CLI (`manage`, `db copy`, `dev anonymize`; the rest are stubs), ruff, pyright strict,
  import-linter contract, pytest (SQLite default, Postgres opt-in), pre-commit, GitHub Actions (lint, SQLite on Ubuntu and Windows,
  Postgres on Ubuntu), `docker/compose.dev.yaml`, `CLAUDE.md`, Claude Code hooks (ruff on edit, unit tests on stop).
- ✅ Test harnesses: DB portability (missing migrations, constraints, no Postgres-only features, SQLite → Postgres copy), simple reads
  (`tests/simple_reads.py`), and i18n template wrapping.
- ✅ Anonymizer (`il2ks dev anonymize`) plus 4 anonymized fixture missions in `tests/fixtures/logs/` (typical, most bailouts, two mission
  ends, no mission end), checked by a test.

## Iteration 1: PoC / MVP (single server, English)

### Part 1: ingestion ✅ (2026-10-03)
Done and verified end to end on all 210 sample missions (NFR-PERF-5): no failures, idempotent re-runs, `rebuild-aggregates` and `reprocess`
reproduce the same rows with stable IDs. Rules in [13_game_rules.md](13_game_rules.md), internals in [14_ingest_internals.md](14_ingest_internals.md).
- ✅ `core.logparse`: all known Korea event types, a generic fallback for unknown ones (TD-20), warnings capped per kind.
- ✅ `core.replay`: sorties, fates and outcomes (decision trees in doc 13), kills and assists (PvP and PvE), gunners, friendly fire, damage and
  hit breakdowns, timeline, bailout rule v2, suspected early bailouts, disconnects, resupply, parked resets, mission-ended sorties.
  Scenario and golden tests.
- ✅ Score inputs, computed now so later pages need no reprocess: combat role per sortie (air superiority / attack, by loadout), time on
  target, taxi accidents and strafed-on-the-ground losses, air-to-air Elo with prop and jet pools (FR-WEB-19/20).
- ✅ `core.catalog`: Korea object catalog (classes incl. crew and equipment, prop/jet), payloads, auto-registered unknowns, REDFOR/BLUFOR by
  country code.
- ✅ Ingester: discover → parse → replay → persist (level 1) → recompute level 2 per affected player → archive (kept forever). `ingest`,
  `watch`, `reprocess` (all, or `--since/--until`), `rebuild-aggregates`. `IngestRun` with completion reason. Remote log mode (FR-ING-16).
- ✅ Config file with a commented template (`il2ks.example.toml`), daily rotating structured log files (TD-27), vulture dead-code check.

### Part 2: frontend and operations (2026-10-03)
Status legend: ✅ merged, 🔧 in progress (an agent is on it), ⏳ queued. As-built details in [16_web_and_operations.md](16_web_and_operations.md).
- ✅ Web foundation: Pico CSS theme (military palette, light/dark), components, template tags, placeholder assets, style guide, 404/500.
- ✅ Pages: home, mission list/detail, player search, profile (ground-kill breakdown, hall of shame, per-aircraft table), player sorties,
  **sortie detail with timeline** (Open Graph tags for Discord).
- ✅ Page caching keyed on a data version (TD-28).
- ✅ Admin: branding (`SiteSettings`, safe logo upload, coalition emblems), hide player or mission, ingestion status page, object and
  country names.
- ✅ `custom/` template and static overrides (`il2ks custom copy/list/accept`, drift check in doctor).
- ✅ **HTTPS only**: bundled Caddy (domain, IP certificates, internal CA for testing), or bring your own proxy (nginx/IIS/Apache samples).
- ✅ `il2ks setup`, `doctor`, `web`, `run`, `createadmin`, `backup` / `restore` (automatic before migrations and daily), `service`
  (systemd / scheduled task). Install docs (`docs/install.md`), wheel packaging and a PyPI release workflow (⏳ first publish needs the
  maintainer's one-time PyPI/GitHub setup, doc 16).
- ✅ Ground-kill breakdown by category (OQ-33), replay fixes from the end-to-end run, bailout hardening (doc 13).
- ✅ Mission-end rules: outcome `airborne` / landed for sorties the mission end cut off, pilot fate `in_aircraft` + an "ended by mission end"
  flag (maintainer feedback, 2026-10-03); **reprocess all** as an explicit CLI option and an admin button (queued for `watch`).
- ✅ **Versioned templates** for `custom/` overrides: outdated overrides get a big warning in the admin (maintainer, 2026-10-03; TD-25).
- ✅ OQ-36: destruction before a disconnect is a normal loss (maintainer, 2026-10-03).
- ✅ Playwright harness and smoke tests (the key flows are listed under "Before the public release").
- ✅ Fixes from the Opus review (2026-10-03): Windows child processes orphaned by `schtasks /End`, a stale `run.json` blocking restarts,
  half-swapped restores, restore while running (refused unless `--force`, OQ-71), restore config path; SQLite WAL mode, the "data
  updated" time, admin password validators, Caddyfile validation, ETag details, logo deletion on Windows; Opus review #1 findings
  (installer, setup page, Docker; merged 2026-10-04).
- ✅ Fixes from the Opus review of 2026-10-04 (everything merged since ac9bada): chart axes in non-English languages, score backfill
  ignoring `[marks]`, migration 0011 atomic again (✅), deprecated `utc_date`/`utc_short` aliases, huge `?tour=` values, hidden-player
  sort order on the killboard, heavy JSON columns in list queries, tour-aware scores on the profile.

## Iteration 1.x: Easy install and polish
The maintainer asked (2026-10-03) to build everything up to the end of iteration 2 except the visual assets, which move to right after
the release gate.
- ✅ **Windows installer (option B)**, the top item: one service, Caddy, firewall rules, setup page. Unsigned (no code signing). Merged
  2026-10-04 with the review fixes: service account `NT SERVICE\il2ks` (OQ-41, OQ-68), password via a locked temp file and
  `/ADMINPASSWORDFILE=` (OQ-69), argument quoting, `python -P`; on upgrade it warns in a message box about outdated customized
  templates (log only when silent). ✅ CI-verified on 2026-10-04: silent install, doctor, site, upgrade check, uninstall and the
  real service test (`windows-installer.yml`, workflow_dispatch).
- ✅ First-run **web setup page** for the installer (game folder, domain, admin account): config written write-validate-replace, token not
  in logs, restart after the response.
- ✅ Docker Compose distribution (option A) for Linux/Wine hosts; no setup page in a container (OQ-70). ✅ Docker https smoke on Windows (2026-10-04): Caddy could not start under the compose hardening (file capability + `cap_drop: ALL`); fixed in the image, the smoke test now runs with the same hardening and ingests a fixture.
- ✅ **Ammo breakdown** (FR-WEB-18): per-sortie hits per ammo type, average hits-to-destroy per aircraft type, `/aircraft/` pages; per-ammo
  damage columns hidden (OQ-52). Real caliber names required before the release (maintainer, 2026-10-04).
- ✅ **PvE breakdown** (FR-WEB-21): kills and deaths by counterpart class ("how often does AA get me?").
- ✅ **`web` alongside `ingest`/`watch`** (maintainer, 2026-10-03): `web` takes the writer lock only when migrations are pending.
- ✅ **Whole-row links** in tables (FR-WEB-24, maintainer, 2026-10-03).
- ✅ **Flavor text** (FR-WEB-23): a few tasteful highlight spots, several variants each (two lines replaced, OQ-75).
- ✅ OQ-37 leftovers: the remaining placeholder icons from Tabler + NOTICE.
- ✅ **Regression guards for agent work** (maintainer, 2026-10-04): `il2ks dev check` (fast / quick / `--full`) is the one pre-commit gate
  for agents, hooks, pre-commit and CI; released migrations can't be edited; template compile test; Claude Code hooks (doc 08).
- ✅ **Ingest speed, round 1** (maintainer, 2026-10-04): compiled-regex tokenizer fast path (the generic tokenizer stays the fallback
  and reference), per-row UPDATEs instead of `bulk_update`, fewer WAL checkpoints: the 210 samples went from 842 s to 344 s (median
  3.4 s → 1.3 s per mission), identical rows. `il2ks dev bench-ingest` / `dump-db`. ✅ Round 2: about 25–40% less CPU per mission;
  ✅ explosion bursts in one event (about 35% less parse time).
- ✅ **Performance tests for the pages** (maintainer, 2026-10-04): server-side timing budgets next to the query budgets, a Locust load
  test (manual), front-end budgets (page weight, requests, LCP/CLS) in the Playwright job.

## Maintainer requests (2026-10-04)
- ✅ **Real ammo names** (FR-WEB-18): `ammo.csv` maps the game's names to plain ones (".50 BMG API", ".50 BMG INC", ".50 BMG API-T",
  "23×115 mm HEI-T", "HVAR 5 in"), with the real designation (M8 API) as a tooltip; not translated. Two Soviet sub-types are inferred
  (OQ-76).
- ✅ **Idiomatic translations**: flavor text is translated for meaning and tone, not word for word (TD-24 drafts). All 1,000 strings drafted in five languages (2026-10-04), UI strings by context, address forms per language; human
  review by native speakers is still open.
- ✅ **Hall of shame**: "Strafed on the ground" moved to "Other totals" (OQ-73); the second tile is **friendly-fire incidents** (OQ-72).
  The quip depends on the kinds of incidents (taxi only, friendly fire only, both, none), with a warmer variant above the 90th percentile
  (OQ-74).

## Decisions to apply (maintainer answers, 2026-10-03; doc 02 "Maintainer decisions")
Do these while merging the finished branches, before the release where they touch release items:
- ✅ Local time: locale-native formats; zone only in the footer; show when the next tour starts.
- ✅ Tours: current tour by default with an all-time toggle; "Sorties in <tour>" framing; flavor text for an empty tour.
- ✅ Ammo: hide the per-ammo damage columns (keep hits).
- ✅ Killboard: `assists` config toggle, off by default. Streaks: a per-player "best streaks" tab. Per-tour killboard and streaks.
- ✅ Score: percentage penalties by outcome (death 80%, capture 50%, plane lost 20%, configurable; OQ-67), flat friendly-fire and early-bailout penalties.
- ✅ Leaderboards: Elo (jet, prop) and ground proficiency on the home page; the rest on the leaderboards page.
- ✅ Aircraft: rank a type's top pilots by skill (per-type Elo / ground proficiency); per-type Elo.
- ✅ Doc 15: list every icon file in the designer brief (generated by `il2ks dev assets`; a test fails on drift).
- ✅ Rams: tested on the samples (17 rams, 13 between enemies, no false positive found); `credit_rams` defaults to on
  (maintainer, OQ-89: rams count for both pilots).
- ✅ Windows installer: virtual service account `NT SERVICE\il2ks` (OQ-41).
- ✅ **Run the tests in parallel** (maintainer, 2026-10-03): pytest-xdist in `il2ks dev check`; the full suite went from ~11 min to ~2 min.

## Before the public release (maintainer, 2026-10-03)
- ✅ **Playwright end-to-end tests on the key flows** (30 tests): (1) a player opens their latest sortie and follows the link to an enemy's
  sortie; (2) someone browses several missions and opens a couple of sorties; plus search → profile → sortie and mission → find yourself →
  sortie.
- ✅ **Times in the viewer's local timezone** (FR-WEB-17; TD-15 as built; OQ-42..44).
- ✅ **Bailout rule v3** (FR-ING-14, doc 13 "as built"): ejection spawn (Rufus's method 1) and the pilot-not-dead gate. Servers need
  `il2ks reprocess --all`. The height arm waits for heightmaps (OQ-39).
- ✅ Stretch, before the release: **stat highlights** (FR-WEB-22): Top 10% / 25% badges against every player with enough sorties.

## Iteration 2: Live data, languages, richer stats
Pulled into the current run (maintainer, 2026-10-03); the release doesn't wait for any of it.
- ✅ **Tours** with configurable length (monthly by default) (TD-26), on the profile, sortie and mission lists; localised titles.
- ✅ **Online now**: current player counts and the list of players, plus in-progress missions (FR-ING-12, FR-WEB-15).
  ✅ `{% online_now %}` on the home page.
- ✅ **Translations** pipeline and LLM drafts for Russian, German, Spanish, French, Brazilian Portuguese (TD-24); ✅ consistency pass
  (2026-10-04: one term per concept, du/vous/вы/tú/você); ⏳ update after the English copy pass, then human review.
- ✅ **Game object names**: admin overrides and translated defaults (TD-24, FR-ADM-5).
- Features from the maintainer's mods, through proper extension points (TD-16):
  - ✅ Score concept (separate air and ground scores), leaderboards and rankings; pages for the air-to-air Elo (prop/jet pools) and ground
    score per hour on target (FR-WEB-19/20); percentage penalties; per-type Elo.
  - ✅ Stats by aircraft (FR-WEB-8). ✅ Split rankings by prop/jet (filter) and fighter/attack (grouped boards).
  - ✅ Killboards (FR-WEB-9), per tour, and ironman streaks with best streaks. ✅ Rams as a `[rules]` toggle (0.5 s / 15 m); a death in the parachute always counts
    (OQ-61).
- ✅ Light charts (FR-WEB-16).

## Public release gate
**Target: roughly 3 weeks from 2026-10-02, around 2026-10-23** (maintainer). These items are **required before it** (the list grows as
decisions are made):
- ✅ Game object names: project-set English defaults plus admin overrides (TD-24, FR-ADM-5).
- ✅ Windows installer (option B), unsigned (doc 07); CI installs, upgrades and runs doctor on a Windows runner.
- ✅ Playwright tests on the key flows, times in the viewer's local timezone, and bailout rule v3 (section above).
- ✅ **Accuracy** (maintainer, 2026-10-04; rounds fired = loaded − left on sorties without a resupply, 66% of samples; overall 6.3%, air 2.8%, ground 7.0%): hit percentage for air-to-air and for ground fire (per sortie, per player, per aircraft
  type). Needs research first: which log fields give the rounds fired (ammo counts at spawn/landing/end, resupply) next to the hits
  we already count (doc 12), how to split air from ground fire, and how gunners and rockets/bombs fit in.
- ✅ **Profile rework** (maintainer, 2026-10-04): split the player page into an **air-to-air** part and an **air-to-ground** part;
  the hall of shame near the top; the **latest 5 sorties** near the top with a "View all sorties" button to the full list.
- ✅ **Two new skill boards, as visible as Elo and ground score per hour** (maintainer, 2026-10-04): **interception** (an air
  superiority pilot's proficiency at shooting down bombers and attackers) and **tank busting** (tanks destroyed per hour on
  target). On the leaderboards next to Elo and ground per hour, and in the home page's top boards. Queued after the leaderboard
  rework lands.
- ✅ **Achievements / medals** (maintainer, 2026-10-04): tiered achievements beyond quips (e.g. 5/10/20/50 air kills in one life,
  X weeks in a row played), computed at ingest, shown prominently on the player page, "earned in this sortie" on the sortie page, an
  overview page. The implemented list and further ideas go to [17_achievements.md](17_achievements.md) for the maintainer's review.
- ✅ **Language selector with flags** (maintainer, 2026-10-04): country flags next to the languages (US for English, Russia,
  Germany, Spain, France, Brazil) so the selector reads as clickable; default to the visitor's OS/browser language when shipped
  (Portuguese variants → Brazilian Portuguese), else English; an explicit choice wins.
- ✅ **Pilot fate shown as Dead / Captured / Survived** (maintainer, 2026-10-04): Dead and Captured override every other fate,
  "unknown" reads as Survived; fate next to the outcome on the sortie previews; Mission is no longer a default column there.
- ✅ **Sortie timeline: damage and hits** (maintainer, 2026-10-04): a column for percent damage taken / given (empty unless the
  event carries it); significant hits (e.g. over 0.1% damage) as timeline rows; ammo used matched to the nearest significant damage
  event. Starts after the ammo-after-loss work lands (same attribution code).
- ✅ **More sortie flavor text** (maintainer, 2026-10-04): quips for more extreme events on the sortie page, e.g. several bombers
  or attackers shot down, lots of assists but no kills ("the kills went to the rest of the flight", in the usual warm tone), and
  other standouts (a very quick first kill, a very long sortie, heavy damage brought home with kills, many ground targets, shot down
  by an AI gunner, a ram). Most notable event wins, several variants each, idiomatic translations.
- ✅ **Ammo used after a loss** (maintainer question, 2026-10-04; release events are commands, not counts: no release = 0 used, else unknown, OQ-101): when the end-of-sortie ammo record comes after the aircraft was
  lost (bailout, climb-out, disconnect), take bombs and rockets used from the release events (exact) instead of showing "unknown"
  for everything; only gun ammo stays unknown. Clearer notice wording ("the game writes it when the sortie ends, after the loss").
- ✅ **Strafed after landing** (maintainer, 2026-10-04): an aircraft that landed and is then destroyed by an attacker on the ground counts as
  strafed even if it was damaged in the air before the landing (today only when every hit came after the landing); crash-landings stay
  "shot down". Plus quips for being strafed on the sortie page. Starts after the assist split (same files).
- ✅ **Explosion bursts in one event** (2026-10-04): consecutive same-tick explosion lines of one attacker are one parser event, about 35% less parse time. Non-std tokenizer libraries evaluated: none is more than 10% faster (regex is about 10% of parse), so none is used.
- ✅ **Sortable sorties with optional columns on the mission page** (maintainer, 2026-10-04): the mission detail page's sortie table sortable by its
  columns (pilot, aircraft, outcome, fate, kills, damage, flight time, …) and with the same optional "Columns" control as the other lists.
- ✅ **Optional columns** on the player, mission and aircraft lists (maintainer, 2026-10-04): the default view stays as it is;
  visitors can add sortable columns (Elo, K/D, scores, …) from a small "Columns" control, kept in the URL.
- ✅ **Stat marks for Elo and scores** (maintainer, 2026-10-04): Top 10% / 25% next to Elo jet/prop, air score, ground score and
  ground score per hour, like the existing ratio marks.
- ✅ **Killboards by aircraft type** (maintainer, 2026-10-04): on the player killboard (and the profile), the enemy types a pilot
  shot down most and the types that killed them most, above the player-vs-player table (more important than it). On the aircraft
  page, a killboard by enemy type with the exchange rate ("how do I counter this plane, what should I fly?"), with a filter for
  intercept flights only (air superiority vs air superiority).
- ✅ **Assists received as a killboard detail (OQ-81) and a page with all of a player's streaks, linked from their sortie page (OQ-82)** (maintainer, 2026-10-04).
- ✅ **More fitting icons** for the gaps in doc 15, other icon sets allowed (OQ-95).
- ✅ **Interception counts transports; strafing needs significant damage by another object after landing (a failed landing of a damaged plane = crashed)** (OQ-102, OQ-112, maintainer 2026-10-04).
- ✅ **Pagination** (OQ-96): 10 missions, 20 rows per page. Exception (maintainer, 2026-10-04): the sortie page's timeline is not paginated (a detail page; a higher
  time and query budget is fine). 🔧 ✅ SVG icon sprite (one cached `/sprite.svg`; real-log mission page 87 → 55 KB).
- ✅ **Home page** tour-aware with six boards (3×2, incl. play time) (OQ-79, OQ-104); Elo "encounters".
- ⏳ **Squash the migrations into one initial migration** (maintainer, 2026-10-04), as the last step before the **first** release
  only (later releases ship their migrations as they are): a new
  database is created in one step instead of replaying the development history (faster installs). Done once, with the
  released-migration guard's maintainer override (`IL2KS_ALLOW_RELEASED_MIGRATION_EDIT=1`); development databases are recreated
  afterwards (or marked applied with `migrate --fake-initial` after checking the schema matches). Backfills in `ops/migrate.py`
  that only exist for pre-release databases can go at the same time.
- ✅ **More branding for server admins** (maintainer, 2026-10-04; incl. `.woff2` font upload): extra links in the top navigation row (up to 30) after the built-in
  ones (Discord, forum, Patreon…), with a recommended maximum measured on real widths (the maintainer guesses 3); custom color schemes
  where nearly every color is a token admins can change, for light and dark; fonts if feasible (self-hosted, no third-party CDN).

- 🔧 **Achievements v2** (maintainer review of doc 17, 2026-10-04, OQ-105): per-tour achievements (reset at tour start, "All time"
  shows all), new achievements (Elo and ground-score milestones, ram, first blood, double/triple/quad kills, aircraft types, landing
  streak, Ace in a Day, hall-of-shame medals), rarity percentage on hover, rarer medals stand out, a recently-earned feed on the home
  page, ribbons for the simpler achievements.
- 🔧 **Column descriptions** (maintainer, 2026-10-04): every column whose meaning is not obvious explains itself on hover (and on
  focus / tap).

**Not gates** (ship when ready, before or after the release): human review of the translations (LLM drafts are in, TD-24), README
screenshots. Everything else the maintainer listed on 2026-10-04 (ammo names, stat marks, iteration 2 items) is required and built.

**Schedule** (2026-10-04): every required item is built except the migration squash, which is the last step before the first release,
and the PyPI first publish (maintainer).

## After the release: reminders
- Revisit the charts (which charts help; maintainer, OQ-59).
- Interactive sortie map, after asking the dev community what data is available (OQ-54/55).
- Per-ammo damage attribution analysis (follow-up damage is hard to attribute; OQ-52).
- Yearly or quarterly aggregates next to tours (OQ-45).
- Bailout height arm once heightmaps arrive (OQ-39).

## Right after the release: visual assets
**Visual assets** ([15_visual_assets.md](15_visual_assets.md)): replace the placeholder icons, aircraft silhouettes, logo, link-preview
image and illustrations with finished ones. Moved here (maintainer, 2026-10-03): the maintainer will work with a designer on a mostly
finished site and wants to release quickly. The site ships with placeholders under the final file names, so this is a drop-in change.


## Later / stretch
- **Live sorties**: stream in-progress data so sorties appear right away (FR-ING-15), in v2–v3 or later.
- Mobile-friendly layout.
- Player accounts, if people ask for them.
- `il2ks ship` helper for remote log mode.
- Optional self-hosted monitoring and error tracking (TD-27).
- Continuous flight tracks from a separate live telemetry source, if the game offers one (TD-08, OQ-26).
- Sortie map: **benched until after the release** (maintainer, 2026-10-03, doc 02 decisions); the grid version stays on its branch, unmerged.
- Stretch: **gunner stats** with the gunner credit rule (FR-WEB-14; needs telling a gunner's fire apart, likely by ammo type).

## Iteration 3: Global stats (multi-server)
- A central instance that receives data from many servers. Each server gets an opt-in exporter (push, or pull through a
  read-only export endpoint), and identities merge through game account UUIDs and `server_uid` (TD-17).
- This is the first time anything leaves the server machine, so it needs a privacy and consent design first.
