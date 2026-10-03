# IL-2 Korea Stats — Design Docs

This folder holds the living design spec for **il2_korea_stats**, an open source, self-hosted
statistics website for IL-2 Sturmovik: Korea dedicated servers. It succeeds `il2_stats`
(the stats system for IL-2 Battle of Stalingrad and its expansions), which no longer works with the new game.

These docs serve two readers:
1. **Future Claude Code instances** working in this repo. They should read this folder before
   making architectural changes, and keep it in sync after.
2. **The maintainer** (and later contributors), who use it to reason about the design.

## Status tags

Every requirement and decision carries one of these tags (they're easy to grep):

| Tag | Meaning |
|---|---|
| `[DECIDED]` | The maintainer has confirmed it. Don't change it without asking. |
| `[PROPOSED]` | Claude recommended it and the maintainer hasn't confirmed or rejected it yet. Treat it as the default but call it out when it matters. |
| `[OPEN]` | Not decided yet. Tracked in [11_open_questions.md](11_open_questions.md). |
| `[DEFERRED]` | Deliberately out of scope for now. See [10_roadmap.md](10_roadmap.md). |

## Index

| File | Contents |
|---|---|
| [01_context_and_goals.md](01_context_and_goals.md) | Background, goals, non-goals, users, pain points |
| [02_functional_requirements.md](02_functional_requirements.md) | What the system must do (ingestion, website, admin) |
| [03_non_functional_requirements.md](03_non_functional_requirements.md) | Install, security, performance, reliability, maintainability |
| [04_architecture.md](04_architecture.md) | Components, layers, data flow, proposed repo layout |
| [05_technical_decisions.md](05_technical_decisions.md) | Decision log (stack, versions, patterns) with rationale and alternatives |
| [06_data_model.md](06_data_model.md) | Preliminary entities and identity rules |
| [07_deployment_and_installation.md](07_deployment_and_installation.md) | Packaging options and recommendation |
| [08_development_workflow.md](08_development_workflow.md) | uv, testing strategy, CI, working with Claude Code |
| [09_legacy_system_notes.md](09_legacy_system_notes.md) | Analysis of `il2_stats`: what to reuse and what to avoid |
| [10_roadmap.md](10_roadmap.md) | Iterations: MVP, follow-ups, global stats |
| [11_open_questions.md](11_open_questions.md) | Unresolved questions, in priority order |
| [12_korea_log_format.md](12_korea_log_format.md) | **IL-2 Korea log format as observed in real logs**: event types, changes from BoS, known game bugs, why il2_stats fails |
| [13_game_rules.md](13_game_rules.md) | **Replay rulebook**: objects, sortie scope, pilot fate and outcome decision trees, loss and death, kill credit, gunners, friendly fire, resupply |
| [14_ingest_internals.md](14_ingest_internals.md) | Parser, catalog, ingest jobs (config, discovery, archive, lock, CLI) and persistence as implemented |
| [15_visual_assets.md](15_visual_assets.md) | **Designer brief**: icons, images and textures the site uses (placeholders until a designer makes them), with file names, sizes and priorities |

Real sample logs live in `../sample_data/`. They're gitignored and contain player data: **never commit them**.

## Rules for keeping these docs useful

- When a decision changes, update its entry in place, set its status, and add a dated one-line note.
  Don't leave stale text behind.
- When an open question is answered, write the answer into the relevant doc and **delete the question** from
  [11_open_questions.md](11_open_questions.md) (or cut it down to what's still open). Never reuse or renumber OQ IDs.
- Prefer short, concrete statements over prose. Link to code once it exists.
- Revisions: 2026-10-02 (initial requirements session). 2026-10-02 (sample-log analysis, SQLite + Postgres,
  Windows research, v1 player stats, rules baseline).
  2026-10-02 (maintainer answered most open questions: tours, HTTPS only, pre-aggregated read models, customization,
  i18n plan, Windows, one install per server).
  2026-10-02 (bailout rule v2 validated on sample data, tours moved to it2, ratios computed at read time, observability,
  Caddy + nginx/IIS, no flight track, mission-end sortie handling).
  2026-10-02 (bailout rule v2 accepted, F-86 structural-failure finding, FR-ING-17 self-destruction definition v2 tested,
  `design-doc-sync` Claude Code skill added).
  2026-10-02 (player profile links to the player's sortie list; FR-WEB-5 decided).
  2026-10-02 (streaming-ready replay interface: feed/snapshot/finish, TD-07).
  2026-10-02 (SQLite is the only user-facing database; Postgres is kept working on the dev side only, TD-04).
  2026-10-02 (granian chosen as the web server, TD-10).
  2026-10-02 (times in the viewer's local timezone, FR-WEB-17: a stretch goal, not in the PoC).
  2026-10-02 (NOTICE file with MIT attribution to the il2_stats authors, TD-18).
  2026-10-02 (game object names: project-set defaults incl. translations, admin overrides, TD-24).
  2026-10-02 (object-name overrides and translations moved to it2 and required for public release; public release gate added; installer ships unsigned).
  2026-10-02 (public release target: ~3 weeks, around 2026-10-23; schedule risk noted in roadmap).
  2026-10-02 (tests default to SQLite everywhere; Postgres opt-in, one CI job, TD-19).
  2026-10-02 (payload names from korea_payloads.csv: 99.3% coverage, F-86 alias needed; OQ-25 on source and license).
  2026-10-02 (data checks: DB constraints + pytest + opt-in sample-data distribution checks; no dbt, no pandera, TD-12).
  2026-10-02 (Playwright end-to-end tests deferred to a later iteration).
  2026-10-02 (repo going public within days; CI on GitHub-hosted runners is free).
  2026-10-02 (ruff confirmed; mandatory tight type hints; pyright strict proposed, TD-12).
  2026-10-02 (Linux admins use the pip/uv path from v1: PyPI package, systemd unit, Wine log paths).
  2026-10-02 (performance not a priority: ≤ ~5 min per mission is fine; no year-of-data targets, NFR-PERF).
  2026-10-02 (ammo breakdown in iteration 1.x with closest-hit damage attribution, FR-WEB-18; not a release gate; explosion attribution checked on samples).
  2026-10-02 (translations other than English are not a release gate; release gate trimmed to object-name overrides + installer).
  2026-10-02 (future position source: separate live telemetry collector and storage, OQ-26).
  2026-10-02 (countries display as plain REDFOR/BLUFOR, no country codes in the UI).
  2026-10-02 (review fixes: stable URLs via natural-key upserts, archive-first + reconcile, late-part re-ingest, retry backoff,
  single-writer lock, move originals after archive by default, admin-state backups; all proposed).
  2026-10-02 (disconnect mid-flight = death; abandoned-aircraft kill credit; Kill table PvP only; safe logo uploads; DST and tour-timezone rules).
  2026-10-02 (iteration 0 done: repo skeleton, harnesses, anonymized fixtures; work happens on main).
  2026-10-03 (disconnect = death only with damage in the last 2 min, any source; "explosion" never shown, ordnance name instead).
  2026-10-03 (explosion lines checked: 95% have no named ordnance line nearby, 99% come from ordnance carriers; labelling rule proposed).
  2026-10-03 (explosion lines never counted as hits; ordnance counted as damaged targets per detonation).
  2026-10-03 (hits-to-destroy per aircraft type counts gun hits only; revisit if air-to-air missiles arrive).
  2026-10-03 (payload file may be redistributed: shipped as core/catalog/data/payloads.csv; OQ-25 reduced to WM names and gaps).
  2026-10-03 (iteration 1 review: OQ-I1 batch resolved into new docs 13 (game rules, fate/outcome trees) and 14 (ingest internals);
  level 2 recomputed per player; friendly fire, resupply, PvE breakdown, reprocess by date range; score split air/ground, Elo and
  time-on-target ideas; page caching TD-28; sample research on resupply, ordnance, gunners, post-end kills; new OQ-27..31).
  2026-10-03 (OQ-27..32 answered: combat role by loadout, time on target from releases near enemy ground objects, prop/jet Elo computed at
  ingest, 300 s post-end window with a ground and gunner guard, taxi accidents and strafed-on-ground counters, gunner credit rule deferred;
  roadmap: ingestion part of iteration 1 done, frontend next).
