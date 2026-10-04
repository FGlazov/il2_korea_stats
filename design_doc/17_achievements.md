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
  extra read for the one fact the sortie row lacks (bombers and attackers shot down, from `Kill`). `PlayerAchievement(player, key,
  tier, earned_at, sortie, mission)` has **one row per earned tier** (a gold holder also has the bronze and silver rows), unique per
  `(player, key, tier)`; `earned_at` is the end time of the sortie that reached it. All time only. `AchievementHolders(key, tier,
  holders)` holds the per-tier counts of **visible** players for the overview (no counting at request time); `recompute_holders`
  rewrites it after each saved mission, in `rebuild_aggregates` and when an admin hides or shows a player.
- Incremental == rebuild: both run the same pure function over the same ordered sorties (tested). `rebuild_aggregates` fills the
  tables. Upgraded databases: `ops/migrate.py::_backfill_achievements` (marker `achievements` in `SiteSettings.backfills_done`)
  computes them once; it only touches medal rows.
- Display: the profile shows the **highest tier of each earned achievement** (a medal row after the hall of shame, each medal linking
  to the sortie that reached that tier), a per-player list `/players/<pk>/achievements/` (every tier with its date, open tiers
  dimmed), an overview `/achievements/` (tiers and holder counts), a holders page `/achievements/<key>/?tier=N` (visible pilots,
  newest first) and "Earned in this sortie" on the sortie page. Medal icons are placeholder Tabler SVGs in `static/il2ks/img/medal/`
  (one per achievement, listed in the asset manifest and doc 15), tinted per tier by the CSS tokens `--il2-medal-bronze|silver|gold|platinum`.
- Cost: one query more on the profile and on the sortie page; the three new pages are two to four queries each (perf rows added).
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
- **Data older than a tour**: all time only for now.

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

- **Per tour** `[DECIDED]`: achievements reset when a tour starts; "All time" in the tour dropdown shows the all-time set. Pages follow
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

- **Per-tour medals**: the same definitions run over a tour's sorties (the streak code already does this for its best-streak rows),
  with a `tour` column in `PlayerAchievement` (null = all time). Shown as "this tour" next to all time; more motivation each month,
  more rows (players x tours x tiers). Easy extension of the current design: `earn_all` takes any list of sorties.
- **Ribbons vs medals**: ribbons (a small bar of coloured stripes, like military service ribbons) instead of round medals: denser,
  suits a long list on the profile, and is cheap to draw as CSS. Medals are kept for now because they read better with only a few
  earned and for the designer's icons.
- **Rarity percentage** on each tier ("held by 4% of pilots"): easy from `AchievementHolders` and the pilot count, but it needs a
  denominator (pilots with at least a sortie, or active pilots) and changes with every ingest, which makes medals feel unstable.
  A cheap middle way: show the holder count (done) and let the viewer judge.
- **Progress to the next tier**: a bar "7 of 10 air kills in this life" on the full list. Needs the current value stored (a
  `PlayerAchievementProgress` or a `value` column on the top row) and for "in one life" the current life's count; the progress
  functions already return it, only the storage is missing.
- **A global feed** ("recently earned", on the home page): one query over `PlayerAchievement` ordered by `earned_at`; deliberately
  not added yet to avoid one more block on the home page.
- **Notifications / Discord post** when a gold tier is reached: out of scope (there is no outbound integration yet).
- **Configurable thresholds** in `il2ks.toml` (`[achievements]`): the registry is code on purpose (tests pin tier behaviour);
  a server with a few pilots may want lower thresholds. Possible later as scale factors per key.
