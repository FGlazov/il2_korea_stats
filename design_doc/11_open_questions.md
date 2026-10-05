# 11 — Open Questions

Only **unanswered** questions live here, ordered by how much each one blocks or shapes the design.
When a question is answered, write the answer into the relevant doc (requirement, decision, or format doc) and
**delete it from this file**. If it's partly answered, cut it down to the part that's still open.
IDs are never reused or renumbered, so gaps are expected. **Answer by ID.**
Answered IDs are not kept here: grep the ID in the spec docs (OQ-38..66 are summarised in [02](02_functional_requirements.md) "Maintainer decisions", OQ-68..78, OQ-79..113 and OQ-114..123 right after it, each with the doc that holds the rule).


## Needs outside input

**OQ-39 Terrain height for the bailout "> 30 m above ground" test** (owner: maintainer, will try to get heightmaps)
Rufus's rule checks the pilot's teardown height against a heightmap; il2ks has no terrain data for the Korea maps. When heightmaps arrive
(and their licence allows shipping them), add the height arm to bailout rule v3 (doc 13). Until then rule v3 stays as built.

## Needs the maintainer's review

**OQ-124 Admin-configurable achievements: defaults** (built 2026-10-04, `/admin/achievements/`)
Defaults applied: thresholds keep the built-in tier count, whole numbers, strictly increasing, at most 1,000,000; names at most 60 and
descriptions at most 300 characters per language, blank = the built-in (translated) text, no "every language" text; no global switch;
no manual "recompute" button: `watch` applies changed thresholds or re-enabled achievements on its next tick (without `watch`, only
`il2ks rebuild-aggregates` applies them); names, descriptions and on/off apply at once.

**OQ-125 Aircraft page filters: how the scopes combine** (built 2026-10-05, roadmap "every section follows every filter")
Defaults applied: the role toggle and "Intercept sorties only" are independent (before, air superiority implied intercept and attack
hid it); the intercept toggle always shows. Matchups apply the role and mods filter to this type's sortie (the killer's for its kills, the
victim's for its losses); hits to destroy and ammo mixes to the destroyed aircraft's sortie, so AI victims count only under "all roles,
no mod filter". The top pilots by Elo in a narrower scope keep the all-time per-type Elo (not recomputed per scope) and list only pilots
with at least `min_air_superiority_sorties` air-superiority sorties in the scope, with the scope's sortie count and a note.

**OQ-126 Quip spots switched off fall through** (review #9, 2026-10-05)
Default applied: a quip spot that is off (or custom-only with no line in the visitor's language) is skipped and the next matching spot
speaks, e.g. a first-blood sortie with 3 kills shows the ace line when first blood is off; before, such a sortie had no quip. "Battered
victor" off lets "limped home" speak. Also: a gunner sortie's aircraft name is plain text, not a link (gunner turrets have no aircraft page).

**OQ-128 All-time achievements and Elo after the clean-slate tours** (maintainer, 2026-10-05: "maybe all time will take the max of all
achievements")
Proposed: all-time tier = the highest tier reached in any one tour (earned at the earliest such tour's sortie), except the **career**
medals that are plain sums of a counter: Sky Hunter (career kills), Tank Buster, flight hours and the taxi / friendly-fire / strafed
shame medals use the all-time summed counters, so a long-time pilot still reaches career tiers no single month gets to. Everything else
(life kills, survivor, landing streak, Regular weeks in a row, types flown, Ace in a Day, Top Rated, …) is the best single tour, so
Regular and the streak medals visibly shrink where a run crossed a month end. All-time Elo = the max of each tour's **final** (for the
running tour: current) rating, not the in-tour peak; all-time Elo games = the sum over tours. The profile's running streak = the one in
the current tour (zero until the pilot flies in it).

**OQ-129 Top 10% / 25% marks with clean-slate tours** (maintainer unsure)
Proposed: per-tour marks against that tour's pilots (as today); all-time marks against the all-time rows (sums and maxes of the tours),
which stays cheap because it reads one row per player, not the history.

**OQ-130 A pilot or aircraft missing from the selected tour** (built 2026-10-05, maintainer: "views still work fine when switching
between tours and suddenly the player is missing")
Defaults applied: the page says "<name> did not fly in <tour>." (aircraft: "was not flown in"), keeps the tour selector and offers
"Switch to: All time · <the tours it has rows in>" on the same sub-page; the "Quiet skies so far" line is kept for tours that are empty
for everyone. Links to pilots and aircraft keep the tour of the page they sit on; mission and sortie pages link with that mission's
tour. Player search and the online-now list still open the current tour (the notice offers the way out).

**OQ-127 Words for the new admin and live strings** (translation drafts, 2026-10-05; part of the human review)
"Quips" is ru Шутки, de Sprüche, es Ocurrencias, fr Bons mots, pt_BR Piadinhas; the live badge is Идёт / Läuft / En curso / En cours /
Ao vivo; "Tier threshold" is порог / Schwellenwert / umbral / seuil / limite.

## Lower impact (owner: maintainer, outside input)

**OQ-26 Live telemetry for positions (Tacview-style)**
Does the IL-2 Korea DServer (or the client) offer a live telemetry feed or recording, such as Tacview real-time telemetry or ACMI export? Is it
reachable from the server machine, and can its object IDs be mapped to log object IDs? This only matters for a future flight-path map (TD-08).

**OQ-25 Payload data: weapon mods and gaps** (owner: maintainer, will extract the remaining payloads)
Progress (2026-10-04): the maintainer supplied the mod names (`weapon_mods.csv`; WM bit 0 always set, mod k = bit k, verified on
30.7k spawns) and a newer payload table (it replaces the old one for all sorties). Still open: unknown payloads after game updates.
Is there a source for `WM` weapon-modification names, like the payload file? Unknown payloads still occur after game updates. (The F-51D 54–58 row shift is fixed.) Pages must keep working with unknown payloads (they show the raw ID). (Redistribution is settled: the payload file
ships in the repo, doc 12.)

**OQ-1 Config keys that enable text logs in Korea's DServer `startup.cfg`** (owner: maintainer, will ask server operators)
`il2ks doctor` needs them to detect and explain a missing setting. In BoS it was `mission_text_log = 1` and `text_log_folder`.
