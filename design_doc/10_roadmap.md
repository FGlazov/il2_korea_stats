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

### Part 2: frontend and operations (in progress, 2026-10-03)
Status legend: ✅ merged, 🔧 in progress (an agent is on it), ⏳ queued. As-built details in [16_web_and_operations.md](16_web_and_operations.md).
- ✅ Web foundation: Pico CSS theme (military palette, light/dark), components, template tags, placeholder assets, style guide, 404/500.
- 🔧 Pages: home + mission list/detail, player search + profile (ground-kill breakdown, hall of shame, per-aircraft table), player sorties
  + **sortie detail with timeline** (Open Graph tags for Discord).
- ✅ Page caching keyed on a data version (TD-28).
- ✅ Admin: branding (`SiteSettings`, safe logo upload, coalition emblems), hide player or mission, ingestion status page, object and
  country names.
- ✅ `custom/` template and static overrides (`il2ks custom copy/list/accept`, drift check in doctor).
- ✅ **HTTPS only**: bundled Caddy (domain, IP certificates, internal CA for testing), or bring your own proxy (nginx/IIS/Apache samples).
- ✅ `il2ks setup`, `doctor`, `web`, `run`, `createadmin`, `backup` / `restore` (automatic before migrations and daily), `service`
  (systemd / scheduled task). Install docs (`docs/install.md`), wheel packaging and a PyPI release workflow (⏳ first publish needs the
  maintainer's one-time PyPI/GitHub setup, doc 16).
- ✅ Ground-kill breakdown by category (OQ-33), replay fixes from the end-to-end run, bailout hardening (doc 13).
- 🔧 Mission-end rules: outcome `airborne` / landed for sorties the mission end cut off, pilot fate `in_aircraft` + an "ended by mission end"
  flag (maintainer feedback, 2026-10-03); **reprocess all** as an explicit CLI option and an admin button (queued for `watch`).
- 🔧 **Versioned templates** for `custom/` overrides: outdated overrides get a big warning in the admin (maintainer, 2026-10-03; TD-25).
- ⏳ OQ-36 default (destruction before a late disconnect is a loss); OQ-37 default (Tabler placeholder icons).
- ⏳ Stretch: **Playwright end-to-end tests** for the key flows: a player finding their own sortie (search → profile → sortie), and someone
  opening a mission, finding themselves and drilling into a sortie (maintainer, 2026-10-03).

## Iteration 1.x: Easy install and polish
The maintainer asked (2026-10-03) to build all of 1.x except the visual assets now, plus tours, online now and translations from it2.
- 🔧 **Windows installer (option B)**, the top item: one service, Caddy, firewall rules, setup page. Unsigned (no code signing).
- ⏳ First-run **web setup page** for the installer (game folder, domain, admin account).
- ⏳ Docker Compose distribution (option A) for Linux/Wine hosts.
- 🔧 (data side) **Ammo breakdown** (FR-WEB-18): per-sortie hits and damage per ammo type, and average hits-to-destroy per aircraft type, with closest-hit attribution.
  **Not a release gate**: ships when ready, before or after the public release.
- ⏳ **PvE breakdown** (FR-WEB-21): kills and deaths by counterpart class ("how often does AA get me?").
- **Visual assets** ([15_visual_assets.md](15_visual_assets.md)): replace the placeholder icons, aircraft silhouettes, logo, link-preview
  image and illustrations with finished ones (a hired designer, or licensed sets). **Not a release gate, but soon after it** (maintainer,
  2026-10-03). The site ships with placeholders under the final file names, so this is a drop-in change.

## Iteration 2: Live data, languages, richer stats
- 🔧 **Tours** with configurable length (monthly by default) (TD-26). Pulled forward (maintainer, 2026-10-03); pages wire in the tour selector after.
- ⏳ **Online now** (pulled forward, 2026-10-03): current player counts and the list of players, plus in-progress missions on the main page (FR-ING-12, FR-WEB-15).
- ⏳ **Translations** (pulled forward, 2026-10-03; after the pages settle): Russian, German, Spanish, French, Brazilian Portuguese (LLM draft, then human review) (TD-24).
- **Game object names**: admin overrides (required for the public release) and translated defaults (not a release gate) (TD-24, FR-ADM-5).
- Features from the maintainer's mods, through proper extension points (TD-16):
  - Score concept (separate air and ground scores), then leaderboards and rankings. Configurable penalties, including for suspected early
    bailouts. Pages for the air-to-air Elo (prop/jet pools) and ground score per hour on target (FR-WEB-19/20); their inputs are stored
    since iteration 1.
  - Stats by aircraft. Split rankings by aircraft class. **Gunner stats** with the gunner credit rule (FR-WEB-14).
  - Killboards. Ironman / virtual-life stats. Rams, parachute deaths, and other rule toggles.
- Light charts (FR-WEB-16).
- Sortie map page of key events (their positions are stored from v1).

## Public release gate
**Target: roughly 3 weeks from 2026-10-02, around 2026-10-23** (maintainer). These items are **required before it** (the list grows as decisions are made):
- Game object names: project-set English defaults plus admin overrides (TD-24, FR-ADM-5).
- Windows installer (option B), unsigned (doc 07).

**Not gates** (ship when ready, before or after the release): translations into languages other than English (UI and object names, TD-24),
the ammo breakdown (FR-WEB-18), and the finished visual assets (doc 15; due soon after the release).

**Schedule risk** (Claude, 2026-10-02): the gate implies all of iteration 0 and 1, the Windows installer from 1.x, and object-name admin
overrides in about 3 weeks, for one developer with AI help. Still tight, but translations no longer gate it (maintainer, 2026-10-02).
The order that protects the date: (1) parser + replay + ingest with golden tests (✅ 2026-10-03), (2) the five core pages, (3) the installer,
(4) object-name overrides. Translations and the ammo breakdown follow when ready. The rest of it2 (tours, online now, mod features) stays after the release.

## Later / stretch
- **Live sorties**: stream in-progress data so sorties appear right away (FR-ING-15), in v2–v3 or later.
- Mobile-friendly layout.
- Times in the viewer's local timezone (FR-WEB-17). v1 shows UTC.
- Player accounts, if people ask for them.
- `il2ks ship` helper for remote log mode.
- Optional self-hosted monitoring and error tracking (TD-27).
- Playwright end-to-end tests on key flows (doc 08), nice to have.
- Continuous flight tracks from a separate live telemetry source, if the game offers one (TD-08, OQ-26).

## Iteration 3: Global stats (multi-server)
- A central instance that receives data from many servers. Each server gets an opt-in exporter (push, or pull through a
  read-only export endpoint), and identities merge through game account UUIDs and `server_uid` (TD-17).
- This is the first time anything leaves the server machine, so it needs a privacy and consent design first.
