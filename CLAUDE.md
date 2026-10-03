# il2_korea_stats (il2ks)

Self-hosted stats website for IL-2 Sturmovik: Korea dedicated servers: log ingester + SQLite + Django/HTMX.

**Before design-relevant work, use the `design-doc-sync` skill.** `design_doc/` is the source of truth for requirements and
decisions (status tags `[DECIDED]` / `[PROPOSED]` / `[OPEN]` / `[DEFERRED]`). Write decisions back after working.

## Commands
```
uv sync                         # Python 3.13 + deps (the system `python` here is 3.5: always use `uv run`)
uv run pytest                   # all tests, SQLite (default)
uv run pytest tests/unit -q     # fast, no DB
uv run ruff check --fix && uv run ruff format
uv run pyright                  # strict
uv run lint-imports             # core must not import Django
uv run vulture                  # dead code (config in pyproject; intentional leftovers: vulture_whitelist.py, with a reason each)
uv run il2ks manage <cmd>       # Django management (migrate, makemigrations il2ks_db, ...)
IL2KS_TEST_DB=postgres uv run pytest   # opt-in, needs docker/compose.dev.yaml
```

## Rules
- **Layers**: `il2ks.core` (logparse, replay, catalog) is pure Python and never imports Django or outer layers (import-linter).
  All game rules live in `core.replay`. All aggregation happens in `ingest`. Views only do simple reads (TD-22; tests use
  `tests/simple_reads.py`).
- **Types are mandatory and tight**: no missing annotations, no `Any`; `Literal`/`Enum`/`NewType` where they fit; pyright strict.
- **Portable SQL only**: no `django.contrib.postgres`, no raw SQL (TD-19). SQLite ships to users; Postgres is dev-side only.
- **i18n**: wrap every template string in `{% translate %}` (a test enforces it).
- **Changed a built-in template, CSS or JS file?** Run `uv run il2ks dev bump-templates` and commit the result (version
  line + `src/il2ks/web/template_versions.json`); a test fails otherwise. Owners' `custom/` overrides get warned by it (TD-25).
- **Every bug fix gets a regression test.** Reference requirement/decision IDs (FR-ING-14, TD-08) in docstrings where relevant.
- **Never commit `sample_data/`** (real player data). Test fixtures come from `uv run il2ks dev anonymize`; a test checks them.
- Work and commit on `main` (no feature branches for now). Push when you're done working.
