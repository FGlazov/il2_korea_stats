---
name: watch-ci
description: After pushing to il2_korea_stats (or merging a branch into main), watch the GitHub Actions CI run for that push and fix any failure right away. Use it after every `git push`, after merging agent branches, and at the start of a session to check that main is green. CI runs more than the local Windows suite - ubuntu SQLite, Postgres, Playwright e2e and a Docker smoke test - so "passes locally" is not done.
---

# Watch CI after pushing

The maintainer's rule (2026-10-03): keep an eye on GitHub CI after pushing, so a red `main` never goes unnoticed. That evening
CI had been red for hours (Postgres first, then ubuntu, e2e and Docker) while everything passed locally on Windows.

## After every push
1. Find the run for your commit: `gh run list --branch main --limit 3` (the run's head SHA must match `git rev-parse --short HEAD`).
2. Wait for it: `gh run watch <run-id> --exit-status` (run it in the background if you keep working). A full run takes ~5 min.
3. If it fails: `gh run view <run-id> --log-failed` (pipe it to a file; it's long), list the failing tests with
   `grep -E "(FAILED|ERROR) tests"`, fix them, push, and watch again. Don't stack new work on a red `main`.
4. Say in your report whether CI is green for your last push.

`gh` needs to be logged in (`gh auth login`, once per machine). Without it, the public API shows run status but not logs:
`curl -s https://api.github.com/repos/FGlazov/il2_korea_stats/actions/runs?per_page=5`.

## Differences that break CI but not a local Windows run
- **Ubuntu**: no drive letters (`Path("E:\\").drive` is empty), the OS timezone is `Etc/UTC` not `UTC`, case-sensitive paths,
  POSIX permissions. Mark Windows-only tests with `skipif(sys.platform != "win32")` and pin environment-dependent defaults.
- **Postgres** (`IL2KS_TEST_DB=postgres`): enforces `max_length` (SQLite doesn't), deferred-trigger rules in migrations, and
  has no SQLite features: SQLite-only tests (restore, WAL settings) carry `@pytest.mark.sqlite_only`.
- **e2e** (`IL2KS_TEST_E2E=1`, Playwright): runs the real pages; page changes (home layout, labels) break locators.
- **Docker smoke** (`docker/smoke-test.sh`): the image's entrypoint and admin bootstrap.
Run the matching job locally when you touch those areas (Postgres: `docker/compose.dev.yaml`).
