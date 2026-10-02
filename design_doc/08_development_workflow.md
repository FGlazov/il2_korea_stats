# 08 — Development Workflow

## Environment `[DECIDED]` (uv) / `[PROPOSED]` (rest)

```
uv sync                          # create .venv, install deps (incl. dev group)
uv run il2ks setup --dev         # write dev config (SQLite by default), migrate
uv run il2ks ingest --from sample_data/2026-09   # load local sample logs (FR-ING-13)
uv run pytest                    # all tests on SQLite (default, zero setup)
uv run pytest tests/unit         # fast core tests only, no DB

docker compose -f docker/compose.dev.yaml up -d db   # Postgres for dev/tests
IL2KS_TEST_DB=postgres uv run pytest                 # same suite on Postgres
IL2KS_DATABASE_URL=postgres://... uv run il2ks run   # run the app on Postgres

uv run ruff check --fix && uv run ruff format
uv run pyright
uv run il2ks run                 # web + watcher locally
```
- **Two databases, both first-class** (TD-04, TD-19). SQLite is the zero-setup default. Postgres comes from Docker or a native
  local install. CI runs the full suite on both. The DB-portability tests (migrations from zero on both backends, SQLite → Postgres
  copy with identical query results) live in `tests/integration/test_db_portability.py`.
- Dependencies are split into groups in `pyproject.toml`: runtime, `dev` (pytest, ruff, pyright, import-linter, pre-commit).
- Old Python on PATH: this machine's `python` is 3.5 (left over from `il2_stats`). Always go through `uv run`.

## Sample data and fixtures
- `sample_data/` holds real server logs (gitignored, **never commit**). Use it for local exploration, performance tests,
  and an opt-in "big" test run (`pytest -m sample_data`, skipped when the folder is missing, so CI and other contributors still pass).
- Committed fixtures in `tests/fixtures/logs/` are **anonymized excerpts or whole missions**: nicknames → `Player001`,
  UUIDs → deterministic fake UUIDs. A script (`il2ks dev anonymize`) does this, and a test checks fixtures for real-looking
  UUIDs and known nicknames.

## Testing strategy `[DECIDED]` (unit test where possible) / `[PROPOSED]` (layers)

The maintainer will "vibe-code" a lot, so **tests are the main guardrail**. Every bug fix gets a
regression test first.

| Layer | What | Speed | Notes |
|---|---|---|---|
| Parser unit tests | Each event type: a valid line → the expected dataclass. Malformed, truncated, and unknown lines → a warning, not an exception. | ms | Parametrized tables of `(line, expected)` |
| Replay scenario tests | Small hand-written logs (10–50 lines) for one rule each: "took off, shot down by player B, bailed out", "landed at friendly airfield", "disconnected mid-flight", "friendly fire", "killed by AI". | ms | Readable, one scenario per test. This is the rulebook in executable form |
| Streaming equivalence | Feeding a fixture mission event by event, then calling `finish()`, gives exactly the same `MissionResult` as `run()`. Snapshots taken mid-mission never raise. | <1 s | Guards the live path (TD-07) |
| Golden snapshot tests | Full real mission logs → `MissionResult` serialized to JSON, compared with a committed snapshot (for example `syrupy`). Any behavior change shows up as a reviewable diff. | <1 s each | **Anonymize** player names and UUIDs in committed fixtures (privacy). Provide a script to do it |
| Property / fuzz tests (optional) | `hypothesis`: shuffled or garbage input never crashes the parser. | s | Nice to have |
| Ingest integration | Real Postgres. Ingest fixture logs, then assert row counts and key facts. Ingesting twice is idempotent. A failing mission rolls back. | s | pytest-django with a Postgres test DB |
| Query tests | `queries/*` functions return correct totals for the fixture data. | s | |
| View smoke tests | Every URL returns 200 against the fixture DB and contains key text. HTMX endpoints return fragments. Each view stays under a query budget and its captured SQL has no `GROUP BY` or aggregates (TD-22). | s | Catches template errors and creeping read-time aggregation |
| Aggregate rebuild tests | Incrementally built level-2 tables equal a full `rebuild-aggregates` from level 1 (TD-08). | s | Guards against counter drift, the old system's main bug class |
| Override tests | A template in a temporary `custom/` folder overrides the built-in one. Branding settings show up in the page CSS (TD-25). | s | |
| End-to-end (later) | Playwright on 2–3 key flows. | slow | Only if the front end grows |

The coverage target applies to `core/`: at least 90%, enforced in CI. Elsewhere coverage is just reported.

## CI `[PROPOSED]`
GitHub Actions on push and PR: ruff, pyright, import-linter, pytest (unit + integration), in this matrix:
**SQLite on ubuntu and windows** (most target hosts run Windows) **plus Postgres on ubuntu** (as a service container). Later: build Docker image and installer artifacts on tags.

## Working with Claude Code

The maintainer asked whether to use Claude skills or something else. Recommendation, in priority order:

1. **`CLAUDE.md` at the repo root** (highest impact, do it first). Keep it short: what the project is, the
   commands above, the layer rule ("core/ never imports django"), "read design_doc/ before architectural changes
   and update it after", "every bug fix gets a regression test", naming conventions, and where things live.
   Claude reads it automatically every session.
2. **Hooks** in `.claude/settings.json`. These are deterministic: the harness runs them, so the model can't forget.
   - After edits: run `ruff format` and `ruff check --fix` on the changed file.
   - Before finishing a turn (Stop hook): run the fast unit tests (`pytest tests/unit -q`) and report failures back.
   - This turns "please remember to lint and test" into an enforced rule.
3. **Tests + CI** (above). These are the real safety net, whatever tool wrote the code.
4. **Project skills** (`.claude/skills/`), added once a workflow repeats. Good candidates:
   - `add-log-event`: a new event type end to end (dataclass, regex, parser test, replay handler, scenario test).
   - `add-page`: a new page (query function, view, template, URL, smoke test) following conventions.
   - `update-object-catalog`: add new aircraft or objects after a game patch.
   - `golden-update`: regenerate snapshot tests and summarize the behavior diff for review.
   Skills are packaged instructions loaded on demand, so they keep `CLAUDE.md` small. Write one after doing a task twice, not before.
   - **Exists now:** `design-doc-sync` (`.claude/skills/design-doc-sync/`). It checks `design_doc/` for changes since the last review
     (including uncommitted edits by the maintainer) before design-relevant work, and writes decisions back afterwards.
5. **Review**: use `/code-review` on branches before merging, and a `/security-review` pass before releases.

## Conventions `[PROPOSED]`
- English everywhere. Type hints required. Use dataclasses or Django models; no bare dicts across layer boundaries.
- Requirement IDs (`FR-WEB-6`) and decision IDs (`TD-08`) are referenced in docstrings and PR descriptions where relevant.
- Small modules. If a file goes past roughly 400 lines, consider splitting it (the old loader was 800+ lines in one function chain).
