# 11 — Open Questions

Only **unanswered** questions live here, ordered by how much each one blocks or shapes the design.
When a question is answered, write the answer into the relevant doc (requirement, decision, or format doc) and
**delete it from this file**. If it's partly answered, cut it down to the part that's still open.
IDs are never reused or renumbered, so gaps (for example OQ-3 and OQ-5) are expected.

## Blocking / high impact

**OQ-1 Remaining log-format unknowns** (everything else is in [12_korea_log_format.md](12_korea_log_format.md))
- Confirm what AType 27 (debris?) and 28 (engine start?) mean.
- What does the new `MID:` field on AType 12 mean?
- What are the config keys that enable text logs in Korea's DServer `startup.cfg`? (`il2ks doctor` needs them)
- ATypes 22, 23 and 29 weren't seen in the samples. When do they appear?

**OQ-2 Confirm the host OS with server operators** (asked on Discord, waiting for answers)
Research says Windows, and we're treating it as primary (see [07](07_deployment_and_installation.md)). Still to confirm: Windows Server
or desktop Windows? Do admins have admin rights (needed to install a service)? Does anyone run the Korea DServer under Wine on Linux?

**OQ-4 Tours and gunners**
- Do we need tours or campaigns (grouping missions into periods) at all? The old system split every stat by tour. If yes, how are they
  defined: by month, manually, or by mission win?
- Do gunners count as separate players with their own stats? Korea has player gunners: `Turret_IL10`, 104 sorties in the samples.

**OQ-19 How do we infer player bailouts and ejections?** *(new, high impact)*
Korea never logs AType 18 for player pilots (verified: 0 of 15,349 sorties). Bailout matters for outcomes (bailed out vs died vs
captured) and for K/D-style stats. Options: (a) a heuristic rule from indirect signals, marked "inferred", and research the
signals using `sample_data` (candidates in doc 12); (b) treat it as unknown until the game is fixed, and count those
sorties as "aircraft lost, pilot fate unknown"; (c) both, with (b) as the fallback when the heuristic has low confidence. Is it worth
comparing notes with the other developer who's working on this, or reporting it to the game developers as a bug?

## Medium impact

**OQ-6 Player identity.** Key players by account UUID (`LOGIN`: one person, several nicknames merged) or by profile UUID
(`IDS`: each nickname separate)? Can players request to hide their stats? Is a self-service request needed, or is it admin-only?

**OQ-7 User accounts on the site.** Any player login in v1 (the old system had registration, profile, squads)? Proposed: no.
Only admin accounts.

**OQ-8 Languages.** English only, or Russian, French, Spanish, Korean, and others from the start? Proposed: i18n-ready
templates, English only in v1.

**OQ-9 "Online now" page and live data.** Show who's flying right now from the in-progress mission logs? Do we need
in-progress missions at all, or only finished ones?

**OQ-10 Exposure and HTTPS.** Is the site usually exposed directly from the game server machine to the internet? On what
port? Is HTTPS expected (Caddy guidance), or is HTTP fine?

**OQ-11 Customization by server owners.** Branding only (name, logo, colors, links), or overriding templates too
(the old system had a `custom/` template override folder)?

**OQ-12 Scope of games.** Korea only, or should the parser also handle BoS-family logs (very similar format, `VER:17`)
as a potential replacement for the aging il2_stats? Proposed: Korea only, keep the parser versioned.

**OQ-13 Visual style.** Pico CSS / minimal, Bootstrap, or a recreation of the old site's look? Are dark mode and mobile-friendly layout required?

**OQ-14 How much event detail to store.** The data now gives us numbers: storing hit and damage rows individually is about 500M rows a year
on a busy server. Proposed: aggregate per sortie and pair, keep the per-sortie timeline and kills, and keep raw archives so finer detail can be
re-extracted. Do we need positions (for a future sortie map) in v1?

**OQ-20 Managed hosting.** Can admins on managed hosts (for example Fox3, which gives RDP access) install and run extra software such as
our stats site on the same machine? If not, do we need a mode where logs are copied to a different machine?

## Lower impact / housekeeping

**OQ-15 Names.** Repo `il2_korea_stats`. Python package and CLI name: `il2ks`? PyPI name?

**OQ-16 Credit and coordination with the il2_stats authors.** Port the MIT code with attribution (TD-18). Should we reach out to
=FB=Vaal and =FB=Isay, or announce on the IL-2 forums?

**OQ-17 Log retention and deletion defaults.** Keep archives forever by default? Delete original logs after processing (the old default was false)?

**OQ-18 Multiple DServers on one machine.** Should one install serve several game servers (several log dirs → one site with a
server filter), or one install per server?

**OQ-21 Weather JSON enrichment.** The sample server writes `*.weather.json` per mission from its own weather tool. Show
weather on the mission page if the file exists? This is server-specific, so it would be an optional feature.

**OQ-22 Country codes.** Which nations are 501, 502 and 503 (communist side) and 601, 602 and 603 (UN side)? This is needed for display names and flags.
Only 501–503 and 601 appear with player spawns in the samples.
