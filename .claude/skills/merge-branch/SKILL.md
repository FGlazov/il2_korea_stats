---
name: merge-branch
description: How to merge a finished agent worktree branch (worktree-agent-*) into il2_korea_stats main without regressions - git merge --no-ff, renumbering migrations after the latest one on main, regenerating po/mo/template_versions.json instead of resolving them by hand, then the full checks. Use it whenever you merge a branch into main (orchestrator role), resolve merge conflicts in migrations/translations/template versions, or after several branches landed and main must be re-verified.
---

# Merging an agent branch into main

Run from the main checkout, on `main`, with a clean tree (`git status`). Merge branches one at a time and run the checks after
each; do not stack merges on a red tree. Everything below stages explicit paths only, never `sample_data/`.

## 1. Look before merging
- `git log --oneline main..<branch>` and `git diff --stat main...<branch>`: what lands, which files overlap with main.
- Migrations on the branch: `git diff --name-status main...<branch> -- src/il2ks/db/migrations`. Only `A` (added) is
  acceptable. An `M`, `D` or `R` of a migration that exists on main means the branch broke the freeze: send it back to the
  agent (put the change in a new migration), do not merge it.

## 2. Merge
`git merge --no-ff <branch>` (a merge commit per branch keeps the history reviewable). On conflicts:
- **Code and docs**: resolve by hand, keep both sides' intent.
- **Generated files: never hand-merge, regenerate** (take either side, then run the generator in step 4):
  `src/il2ks/locale/*/LC_MESSAGES/django.po|.mo` and `src/il2ks/web/template_versions.json`.
  `git checkout --ours -- <file>` (or `--theirs`) just to clear the conflict, `git add <file>`.
- `uv.lock`: take main's, then `uv lock` if the branch added dependencies.

## 3. Renumber migrations (when main gained migrations after the branch point)
`ls src/il2ks/db/migrations` : if the merged-in migration number is not the highest, or two files share a number or there are two
leaves (`uv run il2ks dev check-migrations` says so):
1. Rename the **branch's new** migration (only that one, it was never released) to the next number after the latest on main
   (`git mv 0020_x.py 0021_x.py`).
2. Edit its `dependencies = [("il2ks_db", "<latest on main>")]`.
3. Fix any later migration of the same branch that depended on the old name.
4. `uv run il2ks manage makemigrations --check --dry-run` must print "No changes detected" (if not, the models and the
   migrations disagree: generate a new migration, never edit a released one).
Never touch migrations that were already on main.

## 4. Regenerate and verify
```
uv run il2ks dev bump-templates          # template/CSS/JS versions + template_versions.json
uv run il2ks dev translations update     # .po/.mo for new strings from both sides
uv run il2ks dev check --full            # lint, types, guards, unit + integration (page tests: query budgets)
```
Also `uv run il2ks dev check --full --postgres` when the branch touched models or migrations (docker/compose.dev.yaml), and
`--e2e` when it touched pages. Fix failures before continuing; a failing budget test after a merge means the combined
features cost more queries: fix the cause, do not just raise the number.

## 5. Commit and finish
- Commit regenerated files and renumbering with explicit paths: `git add <paths>`, `git commit` (the merge commit itself, if
  conflicts were open: `git commit --no-edit` after staging).
- Push (when the maintainer's rules allow), then follow the `watch-ci` skill until the run is green.
- Delete the merged worktree/branch only after CI is green.
