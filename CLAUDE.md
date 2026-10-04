# il2_korea_stats (il2ks)

Self-hosted stats website for IL-2 Sturmovik: Korea dedicated servers: log ingester + SQLite + Django/HTMX.

**Before design-relevant work, use the `design-doc-sync` skill.** For runs that span several features or roadmap items, follow the `orchestrate` skill. `design_doc/` is the source of truth for requirements and
decisions (status tags `[DECIDED]` / `[PROPOSED]` / `[OPEN]` / `[DEFERRED]`). Write decisions back after working.

## Commands
```
uv sync                         # Python 3.13 + deps (the system `python` here is 3.5: always use `uv run`)
uv run pytest                   # all tests, SQLite (default)
uv run pytest tests/unit -q     # fast, no DB (add `-n 8 --dist worksteal` to parallelize)
uv run il2ks dev check          # EVERYTHING below + guards, in one go: run before every commit (--fast / --full / --fix)
uv run ruff check --fix && uv run ruff format
uv run pyright                  # strict
uv run lint-imports             # core must not import Django
uv run vulture                  # dead code (config in pyproject; intentional leftovers: vulture_whitelist.py, with a reason each)
uv run il2ks manage <cmd>       # Django management (migrate, makemigrations il2ks_db, ...)
IL2KS_TEST_DB=postgres uv run pytest   # opt-in, needs docker/compose.dev.yaml
```

## Before you commit (imperative; hooks and CI enforce it)
1. Run `uv run il2ks dev check` (quick, ~1-2 min: ruff, format, pyright, import-linter, vulture, `makemigrations --check`,
   released-migration freeze + single leaf, `bump-templates --check`, translations check, template compile, all unit tests).
   It prints what failed and the command that fixes it. `uv run il2ks dev check --fix` applies the auto-fixable parts
   (format, lint, template versions, translations). Add `--fast` for a ~30 s pass without the unit suite.
2. Before you report a task done or hand a branch over, run `uv run il2ks dev check --full` (adds integration/page tests, which
   carry the query budgets). Touched models, migrations or SQL? Also `--postgres` (`docker compose -f docker/compose.dev.yaml up -d db`).
   Touched pages or JS? Also `--e2e`.
3. Never edit, split, rename or delete a migration that exists on `main`/`origin/main`: add a new one. A hook blocks the edit
   and the check fails.
4. Stage explicit paths only (never `git add -A`/`.`, never `sample_data/`), never `--no-verify`, never a bare `git stash`
   (shared by all worktrees; hooks block these). Install the commit hook once: `uv run pre-commit install`.
5. Merging agent branches? Use the `merge-branch` skill. After pushing, use `watch-ci`.

Regression lessons (each one cost a red `main` or a broken database):
- Splitting an applied migration gave InconsistentMigrationHistory on every existing DB: released migrations are frozen.
- Parallel branches each add `0020_*`: after merging there must be exactly one leaf; renumber the later one and fix its dependency.
- A template used a filter that no longer existed (`utc_date`) and only a page test noticed: `test_template_compile` compiles every template.
- Changed templates/CSS/JS without `bump-templates`, or strings without `translations update`: the check fails until you run them.
- Query budgets drift when features merge: run `--full` after merging, and never raise a budget without a reason in the test.
- SQLite hides Postgres failures (varchar `max_length`, deferred trigger events in migrations): run `--postgres` when you touch models/migrations.
- CI stayed red for hours unnoticed: watch the run after every push (`watch-ci`).

## Rules
- **Layers**: `il2ks.core` (logparse, replay, catalog) is pure Python and never imports Django or outer layers (import-linter).
  All game rules live in `core.replay`. All aggregation happens in `ingest`. Views only do simple reads (TD-22; tests use
  `tests/simple_reads.py`).
- **Types are mandatory and tight**: no missing annotations, no `Any`; `Literal`/`Enum`/`NewType` where they fit; pyright strict.
- **Portable SQL only**: no `django.contrib.postgres`, no raw SQL (TD-19). SQLite ships to users; Postgres is dev-side only.
- **i18n**: wrap every template string in `{% translate %}` (a test enforces it). After adding or rewording strings run
  `uv run il2ks dev translations update` (re-extracts, merges into the 5 languages, compiles; a test fails until you do)
  and commit the `.po`/`.mo` changes. Show a `GameObject` as `{{ obj|object_name }}`, never `.display_name`
  (docs/translating.md).
- **Changed a built-in template, CSS or JS file?** Run `uv run il2ks dev bump-templates` and commit the result (version
  line + `src/il2ks/web/template_versions.json`); a test fails otherwise. Owners' `custom/` overrides get warned by it (TD-25).
- **Every bug fix gets a regression test.** Reference requirement/decision IDs (FR-ING-14, TD-08) in docstrings where relevant.
- **Never commit `sample_data/`** (real player data). Test fixtures come from `uv run il2ks dev anonymize`; a test checks them.
- Work and commit on `main` (no feature branches for now). Push when you're done working.
- **After every push, watch the GitHub CI run and fix failures right away** (`watch-ci` skill): CI also runs ubuntu, Postgres, e2e
  and Docker, so a green local Windows run is not enough.
