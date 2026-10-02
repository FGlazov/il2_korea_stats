# 10 — Roadmap

Status: `[PROPOSED]` (ordering) with `[DECIDED]` items marked in the requirement docs. Iterations are ordered by dependency, not by date.

## Iteration 0: Foundations and log discovery
- ✅ Get real IL-2 Korea DServer logs: 210 missions in `sample_data/` (gitignored), 2026-10-02.
- ✅ First version of [12_korea_log_format.md](12_korea_log_format.md).
- ✅ Validated a bailout rule against `sample_data/` (rule v2, FR-ING-14). Accepted for now and to be iterated later. The maintainer will compare notes with the other developer.
- Repo skeleton: `pyproject.toml` (uv), Django project, `il2ks` CLI stub, ruff, pyright, import-linter, pytest
  (SQLite and Postgres), pre-commit, GitHub Actions matrix, dev `compose.yaml` with Postgres, `CLAUDE.md`, and Claude Code hooks.
- Test harnesses from day one: DB portability (TD-19), "views only do simple reads" (TD-22), and i18n string wrapping (TD-24).
- Anonymizer script for test fixture logs. Pick 3–5 representative missions as anonymized fixtures.

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
- `il2ks setup`, `doctor`, `run`, `db copy`. Documented manual install (option C, SQLite default).
- Observability: rotating, structured log files per process (TD-27).

## Iteration 1.x: Easy install and polish
- **Windows installer (option B)**, the top item: one service, Caddy, firewall rules, setup page.
- Docker Compose distribution (option A) for Linux/Wine hosts.
- Performance pass with a year of data (backfill speed, NFR-PERF-4).

## Iteration 2: Live data, languages, richer stats
- **Tours** with configurable length (monthly by default) (TD-26).
- **Online now**: current player counts and the list of players, plus in-progress missions on the main page (FR-ING-12, FR-WEB-15).
- **Translations**: Russian, German, Spanish, French, Brazilian Portuguese (LLM draft, then human review) (TD-24).
- Features from the maintainer's mods, through proper extension points (TD-16):
  - Score concept, then leaderboards and rankings. Configurable penalties, including for suspected early bailouts.
  - Stats by aircraft. Split rankings by aircraft class. **Gunner stats** (FR-WEB-14).
  - Killboards. Ammo or weapon breakdown. Ironman / virtual-life stats. Rams, parachute deaths, and other rule toggles.
- Light charts (FR-WEB-16).
- Sortie map page of key events (their positions are stored from v1).

## Later / stretch
- **Live sorties**: stream in-progress data so sorties appear right away (FR-ING-15), in v2–v3 or later.
- Mobile-friendly layout.
- Player accounts, if people ask for them.
- `il2ks ship` helper for remote log mode.
- Optional self-hosted monitoring and error tracking (TD-27).

## Iteration 3: Global stats (multi-server)
- A central instance that receives data from many servers. Each server gets an opt-in exporter (push, or pull through a
  read-only export endpoint), and identities merge through game account UUIDs and `server_uid` (TD-17).
- This is the first time anything leaves the server machine, so it needs a privacy and consent design first.
