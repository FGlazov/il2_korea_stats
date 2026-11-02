# 11 — Open Questions

Only **unanswered** questions live here, ordered by how much each one blocks or shapes the design.
When a question is answered, write the answer into the relevant doc (requirement, decision, or format doc) and
**delete it from this file**. If it's partly answered, cut it down to the part that's still open.
IDs are never reused or renumbered, so gaps are expected. **Answer by ID.**
Answered IDs are not kept here: grep the ID in the spec docs (OQ-38..66 are summarised in [02](02_functional_requirements.md) "Maintainer decisions", OQ-68..78, OQ-79..113, OQ-114..123, OQ-124..132, OQ-133..135 and OQ-144..146 right after it, each with the doc that holds the rule).


## Needs outside input

**OQ-39 Terrain height for the bailout "> 30 m above ground" test** (owner: maintainer, will try to get heightmaps)
Rufus's rule checks the pilot's teardown height against a heightmap; il2ks has no terrain data for the Korea maps. When heightmaps arrive
(and their licence allows shipping them), add the height arm to bailout rule v3 (doc 13). Until then rule v3 stays as built.

## Needs the maintainer's review

**OQ-154 Early bailout: how much damage makes a bailout fair** (doc 13; maintainer 2026-11-02: the penalty is for bailing out "WITHOUT having taken damage", to prevent denying air kills, and "if ejection happens before any damage should probably be the trigger")
Applied: a suspected early bailout now needs under 20% damage from any source before the aircraft is logged destroyed. The log has no event at the moment the pilot leaves, so that is the closest the data allows. Of 296 suspected bailouts 57 are cleared, all at 49% damage or more; the other 239 had none or only tiny lines. Between 16% and 49% no sortie changes, so the exact number hardly matters. One limit: a wing torn off in a single moment logs no damage before the loss, so it looks like a sound aircraft and stays suspected. Fine?

**OQ-153 Limping Home: the platinum tier** (doc 17; maintainer 2026-11-02: "why does limping home not have a platinum version?")
Applied: it was the only medal with three tiers, for no reason beyond its first definition. Tiers are now 1 / 3 / 5 / 10 (157 / 23 / 5 / 1 of the local pilots), the same as Jet Hunter and First Blood, so ten landings moved from gold to platinum and five is the new gold. No pilot passes ten in a tour. Fine, or keep 10 as gold with a platinum above it?

**OQ-152 Flak Dodger: name and tiers** (doc 17; maintainer 2026-11-02: "charmed life in a ground version would also be a good idea", and a name of its own)
Applied: the ground kills of attack sorties in one life, tiers 100 / 250 / 500 / 1000 (78 / 23 / 9 / 0 of the 1,150 local pilots, the best at 893), about the shape of Charmed Life's 55 / 11 / 2 / 0. Attack sorties alone count: a fighter's strafing adds nothing, as an attack sortie adds nothing to Charmed Life. Do the name and the tiers suit?

**OQ-151 Surgical Strike: name, tiers and minimum** (doc 17; maintainer 2026-11-02: "maybe also top rated for ground efficiency instead of elo?", and a name of its own, not "Top Rated Attacker")
Applied: a new medal next to Top Rated, reading attack proficiency, with tiers of 300 / 600 / 1000 / 2500 points per hour on target (142 / 57 / 15 / 0 of the 1,150 local pilots; the best is at 2,143) and at
least 10 minutes on target in the tour. Is this the "ground efficiency" you meant, and do the name and tiers suit?

**OQ-150 Firestorm: name and tiers** (doc 17; a name of its own, not "Ground Hot Streak"; maintainer 2026-11-02: "maybe also do something like a 'ground hot streak'?")
Applied: most ground kills within 120 s in one sortie, tiers 10 / 25 / 50 / 100 (346 / 201 / 90 / 32 of the 1,150 local pilots). Ground kills come in bunches, so the tiers are far above Hot Streak's 2 / 3 / 4 / 5,
and bronze is common. Static objects count, as they do for the other ground medals. Fine, or higher?

**OQ-149 All-time Jet Hunter: tiers** (doc 17; maintainer 2026-11-02: an all-time Jet Hunter with higher margins)
Applied: the rule of the other career medals, five times the tour tiers, so all time 5 / 15 / 25 / 50 counted over all tours, and the tour medal is now called Tour Jet Hunter. Fine, or other tiers?

**OQ-147 Incident columns and filter: where they apply** (doc 16; maintainer 2026-11-02: "add everything in the details to the extra columns", "filters for taxi accidents/botched landings/getting vulched")
Applied: taxi/takeoff accidents, botched landings and strafed on the ground are optional columns on the mission, aircraft and player lists (the message did not name the list). The filter is one
"Incident" select on the two sortie lists, so one incident at a time and no friendly-fire option. The shares in the summaries have two decimals; the friendly-fire rows stay plain counts. Fine, or change any of these?

**OQ-148 Friendly fire split: what replaced what** (doc 13, doc 16; maintainer 2026-11-02: "split friendly fire kills ... everywhere")
Applied: the total column and tile are gone in favor of "Friendly-fire air kills" and "Friendly-fire ground kills"; the total stays stored for the score penalty, which treats both the same. The sortie
quip stays one spot, and the hall of shame's top-10% variants read two new metrics, one per kind. Should a friendly ground kill keep costing as much as a friendly air kill? Local data: 59 air against 1,033 ground.

**OQ-143 Stats delay: what counts as a detail** (doc 02 FR-ING-25, doc 14 "Stats delay")
Applied: the **side** is held back like the aircraft, so the red/blue counts in online now lag too and a player who joined within the delay has no side yet (a live count per side would show reinforcements).
A name still appears the moment an unknown player first spawns, because the name comes from that line. `delay_minutes` is set in `il2ks.toml` only, not in the admin. The wording of the two notices is a first draft. Fine, or change any of these?

**OQ-142 End a tour and resume the same tour later** (doc 14 "Delete a tour or missions", TD-26; maintainer 2026-10-31: delete only for now)
A tour is one unbroken time range and a mission belongs to the tour holding its start, so a test tour that pauses while a real tour runs and then continues needs a tour with
several time spans (manual mode only; the calendar modes end by the date). That extends TD-26 and touches the activity chart, the wipe's manual boundaries and `retour`. For
repeated tests until then: delete the test tour and start a fresh one. Build it, or is that enough?

**OQ-137 "Delete all data and reprocess": defaults applied** (doc 14 "Delete all data and reprocess", FR-ADM-4)
Applied until you decide: (1) only **superusers** may start it (staff with the reprocess permission may not); (2) while it runs the public pages are empty or partial and
show **no notice** (use the notice banner by hand if you want one); (3) a hidden player or hidden mission whose archive is gone is **dropped** from the hidden lists after
the wipe; (4) a **tour rename survives only when the new
tour has the same start, mode and kind** (a part cut by a decisive mission or not); a tour that moved gets its automatic name. Should any of these change?

**OQ-138 Side balance: wording, placement and the underdog mark** (doc 13 "Side balance", doc 16 "Pages")
Applied: the "Sorties per side" share line is on **every** leaderboard, the Elo boards too, and on the home page under the activity chart (maintainer 2026-10-27: not under the last mission, where it read as that mission's split); the list columns are called "REDFOR pilots" /
"BLUFOR pilots"; the profile line is "Underdog sorties"; the stat mark `underdog_share` **rewards a high share** (a loyal underdog stands out as good). Alternatives: leave the
line off the Elo boards, call the flag something other than "underdog", or show the share neutrally without a good/bad mark.

**OQ-139 Markdown pages: small product choices** (doc 16 "Markdown pages", FR-ADM-2)
Applied: a page link opens in the **same tab** (external links keep the new tab); a navigation link has **either** an address **or** a page; the language of the base text is
the admin's choice (default English); the page title is the `h1` and headings in the text are **not demoted**; tables and strikethrough are on; the footer lists the page
links too. Should any of these differ (for example a page link in a new tab, or only some pages in the footer)?

**OQ-141 Template versions: should added blocks and added CSS/JS selectors be cosmetic?** (doc 05 TD-25, doc 16 "Customization")
Applied: an **added** include, context variable, URL name or static path is cosmetic (M), because an override without it still renders. But an **added `{% block %}`** and an
**added CSS or JS selector** (a class, id, data attribute or custom property) still count as breaking (N): an override of `base.html` that lacks a new block silently loses
what the block carries, and a copied stylesheet lacks the style a new template needs. Should those be M as well, so that fewer owners see the red banner, at the price of
overrides that quietly miss new things? The alternative keeps N for them and relies on `--major` only for what the fingerprint cannot see.

## Lower impact (owner: maintainer, outside input)

**OQ-140 Notice banner, footer and altitude: wording** (doc 16 "Notice banner and footer", "Pages")
Applied: the notice sits right under the header with the notice component's words "Note" / "Warning" and one text for every language; the footer reads "Powered by il2ks <version>"
with the link inside the translated string, worded per language by a draft translation; the altitude column hint says "above sea level, no terrain data"; the Russian draft
translates "Angels" (BLUFOR altitude), the other languages keep it. Is that fine, or should "Angels" stay untranslated everywhere?

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
