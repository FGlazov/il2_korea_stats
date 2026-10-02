# 03 — Non-Functional Requirements

## Installation and operation (pain point P2)

| ID | Requirement | Status |
|---|---|---|
| NFR-INS-1 | A non-programmer can install a working system **without using a terminal**, or at most by copy-pasting a single command. Target: under 15 minutes. | `[PROPOSED]` (target) |
| NFR-INS-2 | No manual database admin: no pgAdmin, no hand-run SQL, no Postgres extensions to enable. | `[PROPOSED]` |
| NFR-INS-3 | Both processes (web and ingester) start with **one action**, and restart automatically after a reboot. | `[PROPOSED]` |
| NFR-INS-4 | Upgrading to a new version takes one action and keeps the data. Migrations run automatically. | `[PROPOSED]` |
| NFR-INS-5 | The machine runs the game server too, so resource use must stay small: idle RAM roughly under 300 MB, and ingestion must not starve DServer of CPU. | `[PROPOSED]` |

## Security (pain point P3)

| ID | Requirement | Status |
|---|---|---|
| NFR-SEC-1 | Use only currently supported versions of Python, Django, SQLite, and dependencies. Document an upgrade policy. | `[DECIDED]` |
| NFR-SEC-2 | Production defaults: `DEBUG=False`, a random `SECRET_KEY` generated at setup, a random DB password generated at setup, and no default credentials anywhere. | `[PROPOSED]` |
| NFR-SEC-3 | Public pages are read-only. The only write paths are the Django admin and the ingester. | `[PROPOSED]` |
| NFR-SEC-4 | The SQLite database and archives sit in a data directory that the web server never serves. (Dev-only Postgres listens on localhost.) | `[PROPOSED]` |
| NFR-SEC-5 | Automated dependency vulnerability checks in CI (for example `pip-audit` / `uv` audit, Dependabot). | `[PROPOSED]` |
| NFR-SEC-6 | **HTTPS only.** HTTP only redirects. HSTS and secure cookies are on. Certificates are obtained and renewed automatically (bundled Caddy), or TLS is terminated by the admin's own proxy (TD-23). | `[DECIDED]` (HTTPS only), `[PROPOSED]` (mechanism) |

## Offline and portability

| ID | Requirement | Status |
|---|---|---|
| NFR-OFF-1 | No outbound network calls at runtime: no CDN assets, no telemetry, no external APIs. Static assets such as htmx and Pico CSS are vendored into the repo. The only exception is ACME certificate issuance and renewal (TD-13). | `[DECIDED]` |
| NFR-OFF-2 | Runs natively on **Windows** (10/11 and Server), the DServer platform. The admin has admin rights. Linux is supported secondarily (Docker, for Wine-hosted DServers). | `[DECIDED]` |

## Performance

| ID | Requirement | Status |
|---|---|---|
| NFR-PERF-1 | Ingest a typical mission (a few hours, around 50 players) in under 30 s. | `[PROPOSED]` |
| NFR-PERF-2 | Public pages render server-side in under 300 ms (p95) with one year of data from a busy server. Achieved by design: views only do simple reads on pre-aggregated tables (TD-22). | `[PROPOSED]` |
| NFR-PERF-4 | **Backfill speed**: reprocessing a year of archived missions (about 3,000) finishes within a few hours on the game machine, at low priority so DServer isn't affected. Mission-level work runs in parallel, and level-2 aggregates get rebuilt once at the end (TD-08). | `[PROPOSED]` |
| NFR-PERF-3 | Expected scale for one busy server (measured from samples): about 8 missions a day, about 73 player sorties per mission, so about 210k sorties a year. Raw logs are about 5.9 MB / 86k lines per mission, including about 61k hit lines and 11k damage lines. Storing hit and damage rows individually would add about 500M rows a year, so **aggregate them per sortie or per pair** (TD-08). This also keeps SQLite comfortable. | `[PROPOSED]` (from data) |

## Reliability and data integrity

| ID | Requirement | Status |
|---|---|---|
| NFR-REL-1 | Each mission is processed in one transaction. A crash halfway leaves no partial data. | `[PROPOSED]` |
| NFR-REL-2 | The ingester recovers by itself after a crash or reboot. Its state lives in the DB, not in memory. | `[PROPOSED]` |
| NFR-REL-3 | One bad mission log doesn't block later missions. It gets marked failed, shown in admin, and can be retried. | `[PROPOSED]` |
| NFR-REL-4 | The full DB can be rebuilt from archived raw logs, which are kept forever (TD-09). | `[DECIDED]` |
| NFR-REL-5 | Format drift (a new game version, unknown ATypes or keys; expected every couple of years) never crashes ingestion. It shows up in admin, and supporting a new event is a local code change (TD-20). | `[DECIDED]` |
| NFR-REL-6 | Switching the database backend (SQLite ↔ Postgres) is a single documented command, covered by an automated test (TD-19). | `[DECIDED]` (tested switchability), `[PROPOSED]` (mechanism) |

## Maintainability (pain point P1)

| ID | Requirement | Status |
|---|---|---|
| NFR-MNT-1 | The parsing and replay core is pure Python with no Django imports, so it can be unit tested in milliseconds. | `[PROPOSED]` |
| NFR-MNT-2 | Type hints throughout, checked in CI. | `[PROPOSED]` |
| NFR-MNT-3 | Lint and format with ruff, enforced in CI and pre-commit. | `[PROPOSED]` |
| NFR-MNT-4 | Unit tests wherever possible. Parser and replay logic aim for at least 90% coverage. | `[DECIDED]` (unit testing), `[PROPOSED]` (coverage target) |
| NFR-MNT-5 | Code, comments, and docs are in English. | `[PROPOSED]` |

## Observability

| ID | Requirement | Status |
|---|---|---|
| NFR-OBS-1 | **v1: log files only.** Each process (web, watcher, Caddy) writes rotating log files in the data directory plus stdout, with a configurable level. Structured (JSON) lines, so a monitoring tool can ingest them later (TD-27). | `[DECIDED]` |
| NFR-OBS-2 | Ingestion run history is visible in admin (see FR-ING-11). | `[PROPOSED]` |
| NFR-OBS-3 | Later: optional, opt-in metrics and error tracking with a self-hostable open-source stack (for example Grafana + Loki + Prometheus, SigNoz, GlitchTip) or Datadog (TD-27). | `[DEFERRED]` |

## Internationalization

| ID | Requirement | Status |
|---|---|---|
| NFR-I18N-1 | Wrap UI strings for translation (Django i18n) from day one. v1 ships English only. Iteration 2 adds Russian, German, Spanish, French, and Brazilian Portuguese (LLM draft, then human review). No Korean (TD-24). | `[DECIDED]` |

## Usability

| ID | Requirement | Status |
|---|---|---|
| NFR-UI-1 | Desktop-first, table-heavy layout (Pico CSS). Mobile-friendly layout is a stretch goal, not a requirement. | `[DECIDED]` |
| NFR-UI-2 | Server owners can rebrand in the admin without touching files, and override any template or static file through `custom/` without forking (TD-25). | `[DECIDED]` |
