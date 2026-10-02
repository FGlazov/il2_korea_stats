# 12 — IL-2 Korea Mission Log Format (observed)

Status: **empirical**. Based on 210 real missions (September 2026, one busy dogfight server: about 15k player
sorties and 18M log lines), plus notes another community developer working on a Korea stats site shared over Discord (2026-10).
Treat it as the best current knowledge and re-verify after game updates. Analysis scripts were throwaway. Rewrite them as
`il2ks` dev tools (`il2ks dev survey-logs`) when the parser exists.

## Sample data (`sample_data/`)

- **Never commit it.** It holds real player nicknames and account UUIDs. It's listed in `.gitignore`.
  Test fixtures must be *anonymized excerpts* (see [08_development_workflow.md](08_development_workflow.md)).
- Layout: `sample_data/2026-09/missionReport(YYYY-MM-DD_HH-MM-SS)[0].txt.zip`. Each zip holds one `.txt` that
  contains the **whole mission** (all parts concatenated). The zips have been extracted in place (about 1.3 GB of `.txt`).
- `*.weather.json` (51 files) are **not DServer output**. A server-side weather randomizer tool on that server
  writes them. Possible later enrichment (sky, wind, temperature per mission). See OQ-21.

## Raw vs archived logs (verified against `il2_stats`)

The maintainer's theory is confirmed:
- **DServer writes plain, unzipped `.txt` files**: `missionReport(<local start time>)[N].txt`, with `N` = 0, 1, 2, …
  A new part starts every ~15 s (based on BoS docs and the old loader's comment that "new logs appear at least every 30 s").
  Evidence in the samples: `T:<n> AType:15 VER:18` appears about 690 times per mission. It's the header line of every part.
- **Zips are made by post-processing**. `il2_stats/src/stats/stats_whore.py::backup_log` concatenates all parts into
  one file named after part `[0]` and writes `<name>.txt.zip` (`ZIP_LZMA`, under `backup/YYYY/M/D/`). The samples follow
  the same convention but use DEFLATE and per-month folders, so the server's own tooling produced them, not stock il2_stats.
- Implication for us: the ingester reads **raw `[N]` part files** in production (FR-ING-1). It should *also* import
  **concatenated archives** (`.txt.zip` or `.txt`), to load history and to use these samples (FR-ING-13).

## Timing

- **50 ticks = 1 s** still holds. The median last tick is 538,811 ≈ 2.99 h, which matches the 3-hour mission rotation visible
  in the file timestamps.
- AType 7 (mission end): 184 files have one, 22 have two, and 4 have none (server crash or restart?). Only 14 of 210 files *end*
  with AType 7 or 19, because cleanup events follow the end marker. **So AType 7 alone isn't a reliable "complete" signal.**
  Proposed: a mission is complete when AType 7 has been seen **or** a newer mission's `[0]` file exists, **or**
  no part has been written for N minutes. Missions without AType 7 still get ingested, with `completed_cleanly = false`.

## Event types

Line format is unchanged from BoS: `T:<tick> AType:<n> KEY:value ...`, ASCII, CRLF.
"Old regex" = `il2_stats/src/mission_report/parse_mission_log_line.py`.

| AType | Count (210 missions) | Meaning | Change vs BoS | Old regex |
|---|---|---|---|---|
| 0 | 210 | Mission start: GDate/GTime, MFile, CNTRS, SETTS, … | `VER` 18. `SETTS` is longer (32 digits). Trailing `ROUNDS`/`POINTS` | ok |
| 1 | 12.9M | Hit: AMMO, AID, TID | **97% are `AMMO:explosion`** (blast/splash). Filter or aggregate early | ok |
| 2 | 2.3M | Damage: DMG (0..1), AID, TID, POS | none seen | ok |
| 3 | 106k | Kill: AID (‑1 = none/environment), TID, POS | none seen | ok |
| 4 | 14.6k | Sortie end: PLID, PID, ammo left | none seen | ok |
| 5 | 11.6k | Takeoff | none | ok |
| 6 | 7.8k | Landing | none | ok |
| 7 | 228 | Mission end | see Timing | ok |
| 8 | 279 | Mission objective result | **new trailing fields** `TARGETS() OBJECTS() PLANES() MTARGETS() MOBJETS()` (sic) | **fails 100%** |
| 9 | 1.2k | Airfield | none | ok |
| 10 | 15.3k | Player spawn: PLID, PID, IDS (profile UUID), LOGIN (account UUID), NAME, TYPE, COUNTRY, FIELD, INAIR, PAYLOAD, FUEL, SKIN, WM | none seen. INAIR: 2 = parking (97%), 1 = runway, 0 = air | ok |
| 11 | 1.1k | Group | none | ok |
| 12 | 2.1M | Object spawn: ID, TYPE, COUNTRY, NAME, PID, POS | **new trailing `MID:<n>`** (‑1 or an id; meaning unknown). Some TYPE names contain commas (`Landing Ship, Tank`). **Also re-emitted for the player's aircraft and pilot right before sortie end** (see below) | fails on comma names |
| 13 | 67k | Influence area: `BC(a,b,c)` | `BC` has **3** values (was 8) | ok |
| 14 | 425 | Area boundary | points are **2D `(x,z)`** (was 3D) | ok (but downstream geometry must handle 2D) |
| 15 | 145k | Log version `VER:18` | once **per log part** (header) | ok |
| 16 | 16.5k | Bot (crew) deinitialization | none | ok |
| 17 | 0 | Position | not seen | — |
| 18 | 799 | Bailout / eject: BOTID, PARENTID | **Only emitted for gunners and AI, never for player pilots.** See below | ok |
| 19 | 25 | Round end | none | ok |
| 20 / 21 | 9.3k / 7.3k | Player connect / disconnect (USERID, USERNICKID) | none | ok |
| 22 | 0 | Ground vehicle movement (per Discord notes) | not seen in samples | ok |
| 23 | 0 | unknown, never observed | — | — |
| **24** | 194k | **Gun burst fired**: `OBJID POS` | new | rejected (AType > 22) |
| **25** | 13k | **External store released** (bomb, napalm, drop tank): `OBJID POS TID`. TID = the newly spawned store object | new | rejected |
| **26** | 5.3k | **Rocket salvo fired**: `OBJID POS TID`. TID = rocket object | new | rejected |
| **27** | 14k | **Probably airframe debris / part separation**. In player sequences it follows the aircraft being killed (`3 → 27 → 31 → 27`) | new, meaning unconfirmed | rejected |
| **28** | 16.6k | **Probably engine start**. Seen in 4,858 of 5,033 parking spawns (engine off), 1 of 67 runway spawns (engine on), and between spawn and wheels-off | new, strong evidence | rejected |
| 29 | 0 | unknown, never observed | — | — |
| **30** | 11.7k | **Wheels off ground**: `ID POS`. Fires about 4 s before AType 5 takeoff | new | rejected |
| **31** | 22.5k | **Wheels on ground**: `ID POS`. Fires at spawn and before AType 6 landing | new | rejected |

The meanings of 24–31 match the Discord notes from the other developer. Our sequence analysis independently confirmed
24, 25, 26, 30 and 31, and backs the inferences for 27 and 28.

### Typical player sortie sequences (event types, deduplicated; `b` = pilot-bot event)
```
landed:     10 b10 31 [28] 30 5 31 6 12 b12 4 b4 b16
shot down:  10 b10 31 [28] 30 5 12 b12 4 b4 b16 12 3(victim)
no takeoff: 10 b10 31 [28] 12 b12 4 b4 b16
```

## Known problems

### No pilot bailout / ejection event (AType 18) for players
- Verified: **0 of 15,349** player sorties have an AType 18 for the player's pilot. That includes all 3,593 sorties where the
  player's pilot was killed. All 799 AType 18 events belong to gunners or AI. In 60 missions there were only 17 parachute
  objects (`CParachute`), all non-player.
- The other developer hopes it's a game bug that'll get fixed, and currently guesses bailouts from indirect signals.
- Design consequence (`[PROPOSED]`): bailout is an **inferred** fact with a confidence or method field, in an isolated
  replay rule that's easy to switch to the real AType 18 if the game adds it. Candidate signals to research: the pilot bot
  deinitializes (16) while the aircraft is airborne and far from where the aircraft ends up, the aircraft keeps flying or crashes
  *after* the pilot's sortie ended, altitude and speed at sortie end, and AType 12 re-emission patterns. See OQ-19.

### AType 12 re-declares player objects at sortie end
- Just before AType 4 (sortie end), the log re-emits AType 12 for the **player's aircraft and pilot bot**, with the same IDs
  and a non-‑1 `MID`. After that, the aircraft can still take damage or be destroyed (`AID:-1`), for example when it's left on
  the ground or burning.
- The legacy `MissionReport.event_game_object` unconditionally replaces the object on AType 12, which breaks
  the link to the sortie. That's very likely why legacy replay misclassifies most deaths (below).
- Our replay must treat AType 12 for a known ID as an **update or re-link, not a new object**, and must decide whether
  post-sortie damage counts toward the sortie.

## Why `il2_stats` fails on Korea logs (verified by running its replay on 20 sample missions)
1. **Crash**: `KeyError` in `report.py:446 Object.__init__` (`mission.objects[log_name]`). None of the Korea objects
   (`mig-15bis`, `f-86a-5`, `platform car aa m1919`, `m46 patton`, …) are in `objects.csv`. **20 of 20 missions abort.**
2. With unknown objects stubbed out, all 20 complete but **outcomes are wrong**: of about 1,400 sorties, 658 end "in_flight",
   only 13 "shotdown" and 12 "crashed", and 0 bailouts, even though there are about 17 player pilot deaths per mission. The likely causes are the
   AType 12 re-declaration and the missing AType 18.
3. Parse failures: all AType 8 lines, AType 12 names with commas, and new ATypes 24–31 get dropped with warnings.

## Countries and coalitions
- `CNTRS:0:0,501:1,502:1,503:1,601:2[,602:2,603:2]`. **Coalition 1 = countries 501–503** (MiG-15bis, IL-10, Yak-9P,
  La-11 → communist side). **Coalition 2 = 601–603** (F-51D, F-80C-10, F-84E, F-86A-5 → UN side). Only 601 had player
  spawns. We don't know yet which nations the codes 501/502/503/602/603 stand for (OQ-22).
- Always read coalition membership from each mission's `CNTRS`. Never hard-code it.

## Objects seen
- Player aircraft (by sorties): MiG-15bis 3,338, F-86A-5 2,755, F-80C-10 2,514, F-51D 2,482, IL-10 1,584, F-84E 1,216,
  Yak-9P 690, La-11 666, plus `Turret_IL10` (player gunners) 104.
- About 370 distinct object types across 20 missions are missing from the old `objects.csv`. Building the catalog is iteration 1 work. Unknown
  objects get auto-registered (FR-ING-7).
- Ammo names follow `BULLET_12-7_USA_API`, `SHELL_23_RUS_HET`, `RKT_127mm_USA_HVAR`, `BOMB_449kg_USA_M65`,
  `NapalmBullet`, `CLUSTER_1kg_RUS_PTAB2_HIT`, … and are parseable into caliber, nation, and type.

## Volume (per mission, median of samples)
About 5.9 MB of text, ~86k lines, ~73 player sorties, ~61k hit lines (97% "explosion"), ~11k damage lines, ~10k object
spawns. For one server running 8 missions a day: ~1.5 GB/month of raw text (archives compress roughly 15×).
