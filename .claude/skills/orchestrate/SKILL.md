---
name: orchestrate
description: How the maintainer wants large il2_korea_stats work runs done - multi-agent orchestration (at most 8 Sonnet worker agents plus one Opus code reviewer), the main agent owning design_doc/ and the roadmap, and how decisions made along the way are recorded (technical → [PROPOSED], everything else → an open question with its own ID). Use it whenever a task spans several features or roadmap items, when asked to "work through the roadmap", "finish the work", run agents or subagents, or resume a previous run.
---

# Orchestrated runs (maintainer's standing instructions)

Collected from the maintainer's instructions of 2026-10-02/03. They apply to every session, not just the one they were given in.

## Roles
- **You orchestrate.** Split the work into agent tasks, review what comes back, merge it into `main`, and keep `design_doc/`
  (including `10_roadmap.md` status markers ✅ / 🔧 / ⏳) in sync. Don't write most of the code yourself.
- **At most 8 concurrent Sonnet agents** do the work (`model: "sonnet"`), plus **one Opus agent** (`model: "opus"`, read-only,
  no worktree) as an independent code reviewer of what lands on `main`: security, correctness, Windows breakage, doc
  mismatches, ranked findings with confidence. Feed its findings to fix agents.
- **Keep at least 5 agents running** while there is real work (maintainer, 2026-10-04): no busy work, but more in parallel.
  Start the reviewer early, as soon as merged work piles up, rather than late.
- You may veto an agent's technical choice and do it a better way. Make gut-feeling calls; stop only when something is truly
  blocking.

## Agents
- Code-changing agents get `isolation: "worktree"` and non-overlapping file ownership. Commit a small shared contract on main
  first when agents depend on each other. Migrations: let each agent add its own and renumber at merge.
- Read-only research, review and test agents can share the tree.
- Time-box agents (~90–120 min); they report what's done, what's left, test results, and a **Decisions** list.
- Keep the slots busy while there is queued work. Before starting, check `git branch` / `git worktree list` for unmerged
  `worktree-agent-*` branches from earlier runs.
- Merging: follow the `merge-branch` skill (`git merge --no-ff`, renumber migrations, regenerate po/mo/template versions,
  `uv run il2ks dev check --full`). Stage explicit paths only; never stage `sample_data/`.
- Every agent prompt: "run `uv run il2ks dev check` before each commit and `--full` before you report"; the hooks in
  `.claude/settings.json` block edits of released migrations and `sample_data/`.
- Commit and push on `main` freely (no feature branches); never force-push.
- Never split or rename a migration that may already be applied: existing databases fail with InconsistentMigrationHistory.
- `sample_data/` holds the only copy of the real logs: tell every agent to copy the zips to a temp dir before ingesting (the default
  `after_archive = move` empties the logs folder) and never to link to it from a worktree. Check worktrees for junctions before
  `git worktree remove --force` (it deletes through them on Windows). If it is ever wiped, restore from an `archive/2026/09` copy.
- Every **new** error seen anywhere (real-data runs, CI, reviews, logs, manual checks) gets its own regression test, test first
  (maintainer, 2026-10-04): reproduce the exact error, confirm the test fails, commit the failing test alone, then fix in a
  separate commit. The orchestrator checks red → green at merge.
- Translations in agent prompts: German du, French vous, Russian вы (lowercase), Spanish tú, Brazilian Portuguese você; UI
  strings understandable in context; quips and medal names idiomatic first (a native line, not a translation).

## Decisions
A *decision* is anything you or an agent decided that the design doc didn't already specify. Making them is allowed, to keep
moving fast; the maintainer reviews them later.
- **Purely technical**: review it, then record it in the right design doc as `[PROPOSED]`.
- **Everything else** (product, UX, wording, game rules, scoring, what players or admins see): add an **open question with its
  own stable ID** to `design_doc/11_open_questions.md`, stating the default that was applied. The maintainer answers by ID.
- Every agent prompt must ask for the Decisions list, each item tagged TECHNICAL or PRODUCT.

## Long runs
Keep the computer awake for the whole run (user-level `keep-awake` skill), and restart it when it times out.
