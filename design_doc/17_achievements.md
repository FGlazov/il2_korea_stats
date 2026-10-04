# 17 — Achievements / medals (FR-WEB-26)

Built 2026-10-04 on the maintainer's request ("beyond quips": tiered achievements such as 5/10/20/50 air kills in one life or
weeks played in a row, shown prominently on the profile). Reviewed by the maintainer on 2026-10-04 (OQ-105, section "Maintainer review" below). Requirement: [02](02_functional_requirements.md) FR-WEB-26. Rules code: `core/achievements.py`
(pure), ingest: `ingest/achievements.py`, words and display rows: `web/medals.py`, pages: `web/views/achievements.py`.

## How it works

- A **registry** of definitions in code (`core/achievements.py`): `key`, ascending tier `thresholds`, a `unit` and a `progress`
  function. `progress` reads a pilot's counted (pilot-role) sorties in chronological order (spawn time, then id) and returns the best
  value reached **up to and including each sortie** (a running maximum). Tier `n` is first reached at the first sortie whose value
  is at least `thresholds[n-1]`. A tier is never lost, and the same history always gives the same rows.
- **Level 2, computed at ingest** (TD-08, TD-22): `recompute_players` calls `recompute_achievements(chunk)` next to the streaks. One
  extra read for the one fact the sortie row lacks (bombers and attackers shot down, from `Kill`). `PlayerAchievement(player, tour, key,
  tier, earned_at, sortie, mission)` has **one row per earned tier** (a gold holder also has the bronze and silver rows), unique per
  `(player, tour, key, tier)` (two conditional constraints, because `tour` is null for all time); `earned_at` is the end time of
  the sortie that reached it. **Per tour** (OQ-105): the same definitions run twice per player, over all their sorties (`tour` null)
  and over each tour's sorties alone, so a life, a streak or a run of weeks starts fresh in a tour (the streak code does the same
  for its best streaks). A saved mission recomputes the all-time rows and the rows of its old and new tour only. Ribbons and
  medals share the table. `AchievementHolders(tour, key, tier, holders, pilots)` holds, per scope, the counts of **visible**
  players for the overview and the rarity (no counting at request time) and `pilots`, the scope's denominator (visible players with
  at least one sortie in it: `Player` all time, `PlayerTour` per tour) `[PROPOSED]`; `recompute_holders` rewrites it after each saved
  mission, in `rebuild_aggregates` and when an admin hides or shows a player (three counting queries, whatever the number of tours).
- Incremental == rebuild: both run the same pure function over the same ordered sorties (tested). `rebuild_aggregates` fills the
  tables. Upgraded databases: `ops/migrate.py::_backfill_achievements` (marker `achievements` in `SiteSettings.backfills_done`)
  computes them once, and `_backfill_achievement_tours` (marker `achievement_tours`) adds the per-tour rows and the pilot counts to a
  database that had only the all-time medals; both only touch medal rows.
- Display: see "Display" below. In short: the profile shows the **highest tier of each earned achievement**, medals first and
  the ribbon rack after them, each linking to the sortie that reached that tier; a per-player list `/players/<pk>/achievements/`
  (every tier with its date, open tiers dimmed), an overview `/achievements/` (tiers and holder counts), a holders page
  `/achievements/<key>/?tier=N` (visible pilots, newest first), "Earned in this sortie" on the sortie page and "Recently earned"
  on the home page. All of them follow the tour choice (TD-26). Medal icons are placeholder Tabler SVGs in `static/il2ks/img/medal/`
  (one per achievement, listed in the asset manifest and doc 15), tinted per tier by the CSS tokens `--il2-medal-bronze|silver|gold|platinum`.
- Cost: two queries on the profile and the sortie page (the rows, and the holder counts for the rarity), two on the home page
  (the feed rows and the rarity; one when nothing was earned), the three achievement pages three to five each (the tour list too).
  A full rebuild of 1,138 pilots takes about 2 s.

## The first set (12 achievements)

Sample counts: the 210 sample missions (September 2026, 1,138 pilots with at least one sortie): pilots holding **at least** that
tier. Thresholds were chosen from the sample distributions: bronze is something a regular does, silver and gold are rare, platinum is
a long-term or one-off goal (several platinum tiers are not reached in a single month by anyone; a server running for a year will see
them).

| Key | Name | Rule | Tiers (bronze / silver / gold / platinum) | Sample pilots (B / S / G / P) |
|---|---|---|---|---|
| `life_kills` | Charmed Life | Air kills in one life: the kills of every sortie since the last death or capture, the fatal sortie included | 5 / 10 / 20 / 50 | 55 / 11 / 2 / 0 |
| `sortie_kills` | Ace of the Sortie | Most air kills in a single sortie | 2 / 3 / 5 / 7 | 162 / 52 / 5 / 0 |
| `career_kills` | Sky Hunter | Air kills in total | 1 / 10 / 50 / 250 | 419 / 63 / 4 / 0 |
| `strike_hunter` | Bomber Hunter | Bombers and attackers flown by **other pilots** shot down (credited kills, not friendly, not own earlier sortie) | 1 / 3 / 7 / 20 | 88 / 15 / 3 / 0 |
| `tank_buster` | Tank Buster | Tanks destroyed in total | 3 / 10 / 25 / 100 | 38 / 4 / 2 / 1 |
| `ground_sortie` | Target-Rich | Most ground kills in a single sortie (static objects count, like the profile's ground kills) | 20 / 50 / 100 / 200 | 271 / 123 / 57 / 13 |
| `survivor` | Ironman | Sorties survived in a row: the ironman streak (`core/streaks.py`) | 5 / 10 / 25 / 50 | 193 / 40 / 2 / 0 |
| `damaged_landing` | Limping Home | Sorties with at least one kill that ended in a landing with `damage_taken` of 0.5 or more | 1 / 3 / 10 | 157 / 22 / 1 |
| `regular` | Regular | Longest run of consecutive ISO weeks (Monday to Sunday, UTC) with at least one sortie | 2 / 4 / 8 / 16 weeks | 436 / 114 / 0 / 0 |
| `frequent_flyer` | Frequent Flyer | Sorties flown (took off) | 10 / 50 / 200 / 1000 | 324 / 56 / 0 / 0 |
| `flight_hours` | Hours Aloft | Flight hours in total | 1 / 10 / 50 / 200 h | 610 / 103 / 3 / 0 |
| `type_veteran` | Type Veteran | Flight hours in the one aircraft type flown most | 2 / 10 / 30 / 100 h | 293 / 59 / 4 / 0 |

Why `ground_sortie` thresholds are high: 91.6% of ground kills are static objects (doc 13), and the top sorties have 200 to 500; with
single-digit thresholds a third of all pilots would hold it.

## Edge cases

- **Gunners**: only pilot sorties are fed in (like all counters), so a gunner sortie earns nothing and does not extend a streak or a
  week. The kills of a gunner are credited to the pilot by the game log (doc 13) and count for the pilot.
- **Hidden players**: the rows exist (hiding is presentation only, FR-ADM-3, like stat thresholds). A hidden player's profile and
  achievement page are 404, they are left out of holder lists and holder counts (`AchievementHolders` is visible players only; the
  admin hide / show action refreshes it). **Hidden missions**: counted, but the medal does not link to the sortie (404 otherwise).
- **Sorties that never took off** (`not_taken_off`): no sortie flown, no week played, no hours, neither extends nor breaks a streak
  or a life (the streak rule). A death or capture at the parking spot still ends the life / survival run.
- **Mission-end cut-offs and disconnects**: the pilot did not die, so the sortie counts as survived and its kills and hours count; a
  cut-off sortie is not a "landing" for `damaged_landing` (only `outcome = landed` is).
- **Friendly kills** are not in `kills_air` (doc 13) and are excluded from `strike_hunter`; AI aircraft shot down count for the air-kill
  medals (they are in `kills_air`) but **not** for `strike_hunter`, which reads `Kill` rows between two pilots (AI victims have none).
- **Rams**: not used (see ideas): `credit_rams` is on by default (maintainer, OQ-89) but the sortie row carries no ram flag.
- **Death in the same sortie as the kills**: `life_kills` counts the fatal sortie's kills (a life ends with its last breath);
  `survivor` does not count the fatal sortie (the streak rule).
- **Re-ingest / reprocess**: rows are recomputed from the sorties, so a medal built on a mission that is reprocessed with fewer
  kills disappears again; the medal's sortie and time move with the data.
- **Time zones**: weeks and hours are UTC (the same as everything stored); the displayed date follows the viewer (`local_date`).
- **Tours**: a sortie belongs to the tour of its mission (`Mission.tour`); a mission without a tour (a database from before tours,
  until `assign_missing` runs) feeds the all-time rows only. Moving a mission to another tour (reprocess, `--retour`) recomputes
  both tours. A tier earned in a tour is not lost when the next tour starts: the tour's rows stay, the new tour just starts at zero.

## Decisions (for review)

- **PRODUCT** Names are warm and short ("Charmed Life", "Bomber Hunter", "Limping Home", "Target-Rich"); none demeaning. Tier names
  Bronze, Silver, Gold, Platinum (three-tier achievements stop at gold).
- **PRODUCT** Thresholds as in the table; `life_kills` is the maintainer's 5/10/20/50.
- **PRODUCT** The profile shows only the best tier per achievement, in registry order (not by rarity), after the hall of shame; a
  pilot with no medal sees no section at all.
- **PRODUCT** One place for everything on the Players side of the site; the overview is linked from the profile's medal section
  (no new main-nav entry; the streak list is not in the nav either).
- **PRODUCT** "Ironman" is the name of both the streak block and the `survivor` medal on purpose (same rule).

## Maintainer review (2026-10-04, OQ-105)

"I think the achievements as given are good!" The first set, its names and thresholds are `[DECIDED]`. Additions, all to build
before the release (details `[PROPOSED]` where the maintainer did not specify them):

- **Per tour** `[DECIDED]`, built: achievements reset when a tour starts; "All time" in the tour dropdown shows the all-time set. Pages follow
  the site's tour rule (TD-26: no `?tour` = current tour, `?tour=all` = all time). `[PROPOSED]`: `PlayerAchievement.tour` (null = all
  time), the same definitions run over the tour's sorties (a life, a streak, a run of weeks starts fresh in a tour);
  `AchievementHolders` per tour too.
- **New achievements** `[DECIDED]` (rules and thresholds `[PROPOSED]`, from the sample distributions):
  - **Elo**: an "Ace Hunter"-style achievement for a high Elo (jet and prop), and the same for the ground score.
  - **Ram**, **First blood** (the first credited air kill of a mission), **double / triple / quad kills** (several air kills
    within a short window), **types** (different aircraft types flown, which encourages flying more kinds of planes), **landing
    streak** (landings in a row).
  - **Ace in a Day**: the maintainer's name for the many-kills achievement.
  - **Hall of shame medals**: tongue-in-cheek, never demeaning (the hall-of-shame rule), shown with the hall of shame.
- **Rarity** `[DECIDED]`: show the share of pilots holding a tier, as hover text (and on the overview). `[PROPOSED]` denominator: pilots
  with at least one sortie in the selected scope.
- **Rarer medals stand out more** `[DECIDED]`, especially in the feed.
- **A global feed** of recently earned achievements on the home page `[DECIDED]`.
- **Ribbons** for the simpler achievements `[DECIDED]` (a ribbon bar, like service ribbons); medals stay for the hard ones.

## Display (OQ-105)

All `[PROPOSED]` unless marked, built 2026-10-04 after the maintainer review. The tour choice is the site's (TD-26): no `?tour` is
the current tour, `?tour=all` all time, `{% tour_select %}` on the achievement pages; the profile keeps its own selector.

- **Medals and ribbons** `[DECIDED]` (split by `Achievement.kind`, default `medal`): the **hard** achievements are medals (round
  badge with an icon: `life_kills`, `sortie_kills`, `strike_hunter`, `tank_buster`, `ground_sortie`, `survivor`,
  `damaged_landing`, `type_veteran`); the **simple, common** ones are **ribbons** (`career_kills`, `frequent_flyer`, `flight_hours`,
  `regular`): things that mostly need time. A ribbon is a small bar of CSS stripes (a colour pair from the status tokens, one of four
  patterns per achievement) with one pip per tier reached, tinted in the tier colour, plus the name: no new colour tokens. The
  profile shows the medals first and the ribbon rack under them; the lists and the overview show medals first, then ribbons.
  New definitions pick their kind with `kind="medal" | "ribbon"`.
- **Hall of shame** `[DECIDED]`: a definition with `shame=True` is a tongue-in-cheek entry. It renders as a ribbon inside the hall
  of shame block of the profile, never in the medal row or the ribbon rack, and never in the home feed. The overview and the full
  list put such entries last.
- **Rarity** `[DECIDED]`: every tier (medal, ribbon, overview row, list row, holders page) says "Held by N% of pilots" for the
  selected scope. Denominator `[PROPOSED]`: visible pilots with at least one sortie in the scope (a tour: `PlayerTour` rows; all
  time: `Player.sorties > 0`), stored with the holder counts so no page counts anything. Percent shown with one decimal below 10
  and rounded above, "less than 0.1%" under that; a tier nobody holds says "nobody yet". The text is the Pico tooltip
  (`data-tooltip`, which shows on hover and on keyboard or tap focus; the medal is focusable when it is not a link) with the
  achievement's description and tier, and the same text sits in a visually hidden span, because a tooltip alone is not announced.
- **Rarer stands out** `[DECIDED]`, thresholds `[PROPOSED]`: tiers held by under **5%** of the pilots get a ring in their tier
  colour (`rarity--rare`), under **1%** a ring and a glow (`rarity--epic`); in the lists a tinted row. Not applied in a scope with
  fewer than **20** pilots (a small server would glow everywhere). Colours come from the tier tokens, so light and dark both work.
- **Home feed** `[DECIDED]`, rules `[PROPOSED]`: "Recently earned" under the streak list: the newest 8 tiers of visible pilots in
  the selected scope (phones show 4), as compact cards with the pilot, the achievement and tier, the rarity and the date, rarer
  ones with the ring or glow. Left out: hall-of-shame entries, **ribbons below silver** and **medal tiers a fifth or more of the
  pilots hold** (bronze Charmed Life on a busy server would drown the rest). The achievement links to its sortie unless the mission
  is hidden; hidden players are not listed. The block is full width under the board grid, so the 3x2 boards are untouched.
  Cost: 2 reads (the newest 40 candidate rows; the holder counts of the scope), 1 when nothing was earned.
- **Sortie page**: "Earned in this sortie" lists the tiers the sortie reached all time, then (own label) in its tour.

## Ideas and alternatives

Further achievements, and why they are not built yet:

- **Ram** ("Kamikaze"/"Brothers in Arms"): needs a stored ram flag per sortie; rams are credited by default now (maintainer, OQ-89), but no sortie stores a
  ram flag yet. Add a `rams` counter first.
- **First blood of a mission**: needs the first kill of each mission by tick. Doable at ingest (one query over `Kill`), but it
  rewards being there first rather than skill and favours a busy pilot slot; left out.
- **Double / triple kills in a short time** (several kills within N seconds): the `Kill.time` data supports it; needs a window rule
  (OQ) and a per-sortie scan of kills. Good candidate for the next set.
- **Kills in one tour**, **best tour**, **tour champion** (top Elo / score of a tour): needs per-tour medals (below).
- **Elo milestones** (1600 / 1700 / 1800): Elo is replayed globally, medals would have to follow that pass; also shows skill, which
  the stat marks (FR-WEB-22) already do.
- **Types**: "flew N different aircraft types", "kills in each of N types" (collector medals), "first kill with a jet / prop":
  data exists (`PlayerAircraft`), no good threshold knowledge yet.
- **Ground**: kills of each ground category (ships, trains, AAA), "ship sinker", "train wrecker", ground targets per hour on target:
  categories exist (doc 13), counts are small outside a few pilots; wait for real tiers.
- **Landing streak** ("landed N times in a row without damage"), **bail-outs survived**, **never captured**: from existing counters.
- **Rescue / wingman**: needs data we do not have (who escorted whom).
- **Hall-of-shame medals** (taxi accidents, friendly-fire incidents): possible as tongue-in-cheek "ribbons" in the shame block; kept
  separate on purpose so medals stay a positive thing and nobody is shamed by a medal list.
- **Server days**: "first sortie", "N missions flown", "played on N different days": easy, low value.
- **Hours of day / night**, **same mission N times**: needs mission-level data and a meaning.
- **Hidden / secret medals** (unknown until earned) and **seasonal medals** (calendar events): purely presentation, can be added in
  the registry with a flag.

Alternative designs:

- **Per-tour medals**: built (see How it works): `earn_all` runs over a tour's sorties, with a `tour` column in `PlayerAchievement`.
  More rows (players x tours x tiers).
- **Ribbons vs medals**: built as both (see Display): ribbons for the simple achievements, medals for the hard ones.
- **Rarity percentage** on each tier ("held by 4% of pilots"): easy from `AchievementHolders` and the pilot count, but it needs a
  denominator (pilots with at least a sortie, or active pilots) and changes with every ingest, which makes medals feel unstable.
  A cheap middle way: show the holder count (done) and let the viewer judge.
- **Progress to the next tier**: a bar "7 of 10 air kills in this life" on the full list. Needs the current value stored (a
  `PlayerAchievementProgress` or a `value` column on the top row) and for "in one life" the current life's count; the progress
  functions already return it, only the storage is missing.
- **A global feed** ("recently earned", on the home page): built (see Display).
- **Notifications / Discord post** when a gold tier is reached: out of scope (there is no outbound integration yet).
- **Configurable thresholds** in `il2ks.toml` (`[achievements]`): the registry is code on purpose (tests pin tier behaviour);
  a server with a few pilots may want lower thresholds. Possible later as scale factors per key.
