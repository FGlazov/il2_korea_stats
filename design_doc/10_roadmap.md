# 10 — Roadmap

Status: `[PROPOSED]`. Iterations are ordered by dependency, not by date.

## Iteration 0: Foundations and log discovery
- ✅ Get real IL-2 Korea DServer logs: 210 missions in `sample_data/` (gitignored), 2026-10-02.
- ✅ First version of [12_korea_log_format.md](12_korea_log_format.md). Still to do: confirm AType 27/28 and the meaning of `MID`, and design the bailout inference (OQ-19).
- Repo skeleton: `pyproject.toml` (uv), Django project, `il2ks` CLI stub, ruff, pyright, import-linter, pytest
  (SQLite and Postgres), pre-commit, GitHub Actions matrix, dev `compose.yaml` with Postgres, `CLAUDE.md`, and Claude Code hooks.
- DB-portability test harness (TD-19) from day one, so the first model already runs on both backends.
- Anonymizer script for test fixture logs. Pick 3–5 representative missions as anonymized fixtures.

## Iteration 1: PoC / MVP (single server)
- `core.logparse`: all Korea event types, with tests.
- `core.replay`: sorties, outcomes, kills and assists, damage, timeline, with scenario and golden tests.
- `core.catalog`: initial Korea object catalog (aircraft, ground units). Unknown objects get auto-registered.
- Ingester: discover → parse → replay → persist → archive. `ingest`, `watch`, `reprocess`. Records `IngestRun`.
- Web: mission list and detail, player search, player profile, player sorties, **sortie detail with timeline**.
- Admin: site settings, hide player or mission, ingestion status, object catalog.
- `il2ks setup`, `doctor`, `run`, `db copy`. Documented manual install (option C, SQLite default).

## Iteration 1.x: Easy install and polish
- **Windows installer (option B)**, the top item. DServer is a Windows program (OQ-2 confirmation pending).
- Docker Compose distribution (option A) for Linux/Wine hosts.
- HTTPS guidance (Caddy). i18n scaffolding (OQ-8).
- Performance pass with a year of synthetic data.

## Iteration 2: Richer stats (ported from the maintainer's mods via proper extension points, TD-16)
- Score concept, then leaderboards and rankings. Tours or campaigns (OQ-4).
- Stats by aircraft. Split rankings by aircraft class. Gunner stats.
- Killboards. Ammo or weapon breakdown. Ironman / virtual-life stats.
- Configurable rule toggles (bailout penalties, rams, parachute deaths…).
- Sortie map (positions + map tiles) (OQ-14).

## Iteration 3: Global stats (multi-server)
- A central instance that receives facts from many servers. Each server gets an opt-in exporter (push, or pull through a
  read-only export endpoint), and identities merge through game UUIDs and `server_uid` (TD-17).
- This is the first time anything leaves the server machine, so it needs a privacy and consent design first.
