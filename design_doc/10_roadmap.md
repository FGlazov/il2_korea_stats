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
- `core.logparse`: all known Korea event types, a generic fallback for unknown ones (TD-20), with tests.
- `core.replay`: sorties, outcomes, kills and assists, damage, key-event positions, timeline, pilot fate (bailout rule v2, mission-ended sorties), and the suspected early bailout flag.
  Scenario and golden tests.
- `core.catalog`: initial Korea object catalog (aircraft, ground units). Unknown objects get auto-registered. REDFOR/BLUFOR naming.
- Ingester: discover → parse → replay → persist (level 1) → aggregate (level 2) → archive (kept forever). `ingest`, `watch`,
  `reprocess`, `rebuild-aggregates`. Records `IngestRun`. Remote log mode (copy-tolerant folder, FR-ING-16).
- Web (Pico CSS): mission list and detail, player search, player profile (all-time totals, ratios computed at read time, per-aircraft table),
  player sorties, **sortie detail with timeline**.
- Admin: branding (`SiteSettings`), hide player or mission, ingestion status, object, country and coalition names.
- `custom/` template and static overrides.
- **HTTPS only**: bundled Caddy, or bring your own proxy.
- `il2ks setup`, `doctor`, `run`, `db copy`. Documented manual install (option C, SQLite default), **published to PyPI**. It's also the
  **Linux path** (systemd unit, Wine log paths).
- Observability: rotating, structured log files per process (TD-27).

## Iteration 1.x: Easy install and polish
- **Windows installer (option B)**, the top item: one service, Caddy, firewall rules, setup page. Unsigned (no code signing).
- Docker Compose distribution (option A) for Linux/Wine hosts.
- **Ammo breakdown** (FR-WEB-18): per-sortie hits and damage per ammo type, and average hits-to-destroy per aircraft type, with closest-hit attribution.
  **Not a release gate**: ships when ready, before or after the public release.

## Iteration 2: Live data, languages, richer stats
- **Tours** with configurable length (monthly by default) (TD-26).
- **Online now**: current player counts and the list of players, plus in-progress missions on the main page (FR-ING-12, FR-WEB-15).
- **Translations**: Russian, German, Spanish, French, Brazilian Portuguese (LLM draft, then human review) (TD-24).
- **Game object names**: admin overrides (required for the public release) and translated defaults (not a release gate) (TD-24, FR-ADM-5).
- Features from the maintainer's mods, through proper extension points (TD-16):
  - Score concept, then leaderboards and rankings. Configurable penalties, including for suspected early bailouts.
  - Stats by aircraft. Split rankings by aircraft class. **Gunner stats** (FR-WEB-14).
  - Killboards. Ironman / virtual-life stats. Rams, parachute deaths, and other rule toggles.
- Light charts (FR-WEB-16).
- Sortie map page of key events (their positions are stored from v1).

## Public release gate
**Target: roughly 3 weeks from 2026-10-02, around 2026-10-23** (maintainer). These items are **required before it** (the list grows as decisions are made):
- Game object names: project-set English defaults plus admin overrides (TD-24, FR-ADM-5).
- Windows installer (option B), unsigned (doc 07).

**Not gates** (ship when ready, before or after the release): translations into languages other than English (UI and object names, TD-24),
and the ammo breakdown (FR-WEB-18).

**Schedule risk** (Claude, 2026-10-02): the gate implies all of iteration 0 and 1, the Windows installer from 1.x, and object-name admin
overrides in about 3 weeks, for one developer with AI help. Still tight, but translations no longer gate it (maintainer, 2026-10-02).
The order that protects the date: (1) parser + replay + ingest with golden tests, (2) the five core pages, (3) the installer, (4) object-name
overrides. Translations and the ammo breakdown follow when ready. The rest of it2 (tours, online now, mod features) stays after the release.

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
