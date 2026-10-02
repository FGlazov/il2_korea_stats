# 01 — Context and Goals

## Background

- IL-2 Sturmovik: Korea came out around mid-2026. Its dedicated server (DServer, a Windows program) writes mission logs
  to disk as text files, in the same `T:<tick> AType:<n>` format as the earlier "Great Battles" titles, with
  changes and new event types. **Confirmed with 210 real missions. See [12_korea_log_format.md](12_korea_log_format.md).**
- The community stats system for the earlier games, `il2_stats` (MIT license, by =FB=Vaal and =FB=Isay,
  with community forks and mods), **doesn't work with IL-2 Korea**. Verified: it crashes on every Korea mission (unknown
  objects), and even with that patched it misclassifies most sortie outcomes. Local copy: `../../il2_stats`.
- The project is **inspired by `il2_stats`** by =FB=Vaal and =FB=Isay (TD-18).
- At least one other community member is building a closed-source Korea stats site for their own server, using the
  same "parse logs after each mission into a DB" approach. They shared notes on the log format (see doc 12).
- `il2_stats` has two parts: a long-running log-processing job that parses mission logs into Postgres,
  and a Django website (server-side rendered) that reads from Postgres. Each server admin runs both
  on their own server machine.
- The maintainer is a data scientist and backend developer. They plan to build much of this project
  with AI assistance ("vibe-coding"), so tests and clear structure act as guardrails.

## Goal

Build an open source successor for IL-2 Korea with **the same three components**:

1. **Ingester**: a separate scheduled job that reads DServer logs and loads parsed data into the database. `[DECIDED]`
2. **A relational database**: SQLite or PostgreSQL, both supported, with switching between them tested. `[DECIDED]` (2026-10-02, was Postgres only; see TD-04)
3. **Django website** that serves the stats, mostly server-side rendered. `[DECIDED]`

It should fix the three main pain points of the old system (below), and its design shouldn't block a
future multi-server "global stats" system.

## Pain points to fix (from the maintainer)

| # | Pain point in `il2_stats` | What we want instead |
|---|---|---|
| P1 | 10-year-old codebase with a lot of tech debt and a tangled structure | Clean layers, a pure-Python core, small modules, tests. See [04_architecture.md](04_architecture.md) |
| P2 | Installation is complicated: manual Postgres setup, extensions, Python install, `.cmd` scripts, editing an ini file | A simple install for non-programmers, possibly Docker or an installer. See [07_deployment_and_installation.md](07_deployment_and_installation.md) |
| P3 | Old stack (Python 3.5, Django 1.11, Postgres 9.5, Pillow 6) with known security issues | Current, supported versions, with a policy for staying current. See [05_technical_decisions.md](05_technical_decisions.md) |

## Users

| User | Needs | Notes |
|---|---|---|
| **Player** | "How did my sortie go?" View their own sorties, missions, and profile. | The main v1 use case. Arrives from a link or a search for their nickname. |
| **Server admin** | Install once, point it at the log folder, and forget it. Occasionally hide a player or mission, rebrand the site, or override templates. | Often **not a programmer**. Runs **Windows** with admin rights (DServer is a Windows program). One install per game server. Some owners customize templates heavily (TD-25). |
| **Maintainer / contributors** | Understandable, testable code that's easy to extend with AI assistance. | Uses uv. Comfortable with Python and SQL. Avoids complex front-end tooling. |
| **Future: global stats operator** | Aggregate data from many servers. | Iteration 2 or 3. Only "don't paint ourselves into a corner" matters now. |

## Non-goals (for now)

- Cloud services, external APIs, or telemetry. Everything runs locally on the server machine. `[DECIDED]`
- A rich SPA or JS-heavy front end (React and similar). `[DECIDED]`
- Feature parity with `il2_stats` (awards, squads, tours, user registration, and so on). `[PROPOSED]`.
  We'll add these selectively in later iterations.
- The maintainer's own `il2_stats` mods (`mod_rating_by_type`, `mod_stats_by_aircraft`: split rankings,
  per-aircraft stats, ironman, gunner stats, and so on) are **nice to have after the PoC**, not v1. `[DECIDED]`
  They were built by monkeypatching the core. Their replacements must use real extension points (TD-16).
- Supporting the older Great Battles titles (BoS, BoM, and so on). **Korea only** `[DECIDED]`, even though the log format is similar.
- Player accounts or logins on the site (v1). Maybe later if people ask for it. `[DECIDED]`
