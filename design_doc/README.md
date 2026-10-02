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
