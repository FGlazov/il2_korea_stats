---
name: design-doc-sync
description: Check the il2_korea_stats design docs (design_doc/) for what's decided and what changed since you last looked, before doing any design-relevant work in this repo, then keep the docs in sync afterwards. Use it at the start of any task that writes or changes code, models, migrations, the log parser, replay rules, ingestion, pages, packaging, tests or config. Also use it when planning a feature, when asked "what did we decide about X", and before proposing architecture or stack changes. Use it even when the user doesn't mention the design doc: the maintainer edits it directly, and decisions there override assumptions.
---

# Design doc sync

`design_doc/` is the source of truth for this project's requirements and decisions. The maintainer edits it
directly, often between sessions, so what you remember (or what the code implies) can be out of date. This skill
makes sure you build on the *current* decisions, and that decisions made while working end up back in the docs.

## 1. See what changed

Run the change summary from the repo root:

```bash
bash .claude/skills/design-doc-sync/scripts/design_doc_changes.sh
```

It lists the commits that touched `design_doc/` since the last recorded review, any **uncommitted** edits there
(the maintainer often edits in the IDE without committing), and the remaining open questions. For each changed
file, read the actual diff (`git diff <marker> -- design_doc/<file>`, or `git diff HEAD -- design_doc/<file>` for uncommitted
edits), not just the file name. A one-word status change (`[PROPOSED]` → `[DECIDED]`) can matter more than a new section.

If something changed that affects the task, say so in one or two lines before starting ("Since last time, tours
moved to iteration 2, so I won't add the tour filter").

## 2. Read what the task touches

Always skim `design_doc/README.md` (index, status tags, rules). Then read the docs for the area you're working in:

| Task area | Read |
|---|---|
| Log parsing, new event types | `12_korea_log_format.md`, TD-20 in `05`, `04` (logparse layer) |
| Game rules: kills, outcomes, bailouts, self-destruction | `02` (FR-ING-4, 14, 17 and the rule sections), TD-21, `12` |
| Ingestion, archives, reprocessing | `02` (FR-ING), `04` (pipeline), TD-06/08/09 |
| Models, migrations, queries | `06_data_model.md`, TD-04/08/19/22 |
| Pages, templates, admin | `02` (FR-WEB, FR-ADM), TD-05/22/24/25, `06` (page → table map) |
| Install, HTTPS, services, config | `07`, TD-10/11/14/23, FR-OPS |
| Tests, tooling, CI | `08_development_workflow.md`, TD-12/19/22 |
| Anything optional or "nice to have" | `10_roadmap.md` (is it in this iteration?) |

## 3. Respect the status tags

- `[DECIDED]`: follow it. If the request conflicts with it, point out the conflict and ask before going against it.
  Don't quietly "improve" on a decided item.
- `[PROPOSED]`: use it as the default, and mention it when your work depends on it, so the maintainer can still say no.
- `[OPEN]` or a question in `11_open_questions.md`: don't guess silently. Ask, or make the smallest reversible choice and say so.
- `[DEFERRED]` or a later iteration in the roadmap: don't build it unless asked. Leave seams for it if that's cheap.

## 4. Write decisions back

When the work settles or changes something the docs describe (a new event type, a schema change, a threshold, a
library choice, a rule tweak), update the relevant doc in the same change:
- Edit the entry in place and set its status. Add a dated note if a decision changed. Don't leave stale text behind.
- If you made a call the maintainer hasn't approved, mark it `[PROPOSED]`, not `[DECIDED]`.
- When an open question is answered, write the answer into the relevant doc and delete the question from
  `11_open_questions.md`. Never reuse or renumber IDs.
- Append a one-line entry to the revision list in `design_doc/README.md`.
- Mention the doc updates in your final summary.

Docs only, no code changes? Steps 1–2 still apply, and step 4 is the whole job.

## 5. Record the review

After you've reviewed the changes (step 1), and again after committing doc updates, record where you got to:

```bash
bash .claude/skills/design-doc-sync/scripts/design_doc_changes.sh --mark
```

This writes the current commit to `.claude/.design-doc-last-reviewed` (local, gitignored), so the next session sees
only what's new. Uncommitted edits always show, whatever the marker says.

## Reminders

- `sample_data/` holds real player data. It's gitignored, so never commit or quote raw player names or UUIDs from it.
- Don't trust your memory of a decision when the doc is one read away.
