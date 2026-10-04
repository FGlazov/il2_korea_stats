# 15 — Visual Assets (brief for a designer)

Status: `[PROPOSED]` (2026-10-03). The maintainer will hire a designer once the site is stable. Until then the site ships **simple
placeholder files under the same names**, so finished assets can be dropped in without code changes.

## The site in one paragraph

il2ks is a free, open-source statistics website for **IL-2 Sturmovik: Korea** multiplayer servers (Korean War air combat, 1950–53: early
jets such as the MiG-15 and F-86 Sabre, plus propeller fighters and attack aircraft). Each game server runs its own copy. Players use it
mainly to **look at their own last flight** ("sortie"): what they shot down, who shot them down, a timeline of the flight. It's a calm,
table-heavy site: mostly numbers in tables, some summary tiles, some collapsible sections. Server owners can rebrand it (logo, accent
colour) and override any file.

**Style direction** (maintainer): *vaguely military*: camo or aircraft motifs, **nothing too distracting**. The current placeholder theme
uses muted olive / khaki / gunmetal tones with one restrained accent colour, light and dark modes, squared corners, and a faint texture in
the header only. The old IL-2 stats sites ([combatbox.net](https://combatbox.net/en/), [stats.virtualpilots.fi](https://stats.virtualpilots.fi/en/))
show what such a site does; their look is considered dated. Data areas (tables, numbers) must stay plain and highly readable.

## Technical requirements for every asset

- **Icons: SVG**, 24 × 24 grid, one colour drawn with `currentColor` (the site recolours them for light/dark mode, badges and the server
  owner's accent colour). One consistent style across the whole set (same stroke weight, corner style and level of detail). They must read
  at 16 px and look good at 48 px. No text inside icons (the site is translated into six languages).
- **Illustrations and textures: SVG** where possible, otherwise PNG/WebP at 2× resolution. Textures must tile seamlessly and work at very
  low contrast on both a light and a dark background.
- **Licensing:** the project is MIT-licensed and redistributed on PyPI, so assets must be original work (no game screenshots, no art traced
  from IL-2 or other games, no stock art with restrictions) and delivered under a licence that allows redistribution and modification
  (for example MIT or CC BY 4.0, with the credit line the designer wants in the `NOTICE` file).
- **Coalition emblems** `[DECIDED]` (maintainer, 2026-10-03, OQ-34): **neutral REDFOR / BLUFOR emblems** by default. Real period
  insignia (red stars, the US star-and-bar, UN roundels) may follow **per country** once we know which nation each country code is. As
  state symbols they are generally free of copyright (US federal works are public domain; Russian law excludes state symbols; the roundels
  are simple geometric shapes), but a particular drawing can carry its artist's licence, so redraw them or use files marked public
  domain.
- **Optional insignia** `[DECIDED]` (maintainer, 2026-10-03): the server owner picks each side's emblem in the site settings: **REDFOR**:
  neutral (default), Soviet VVS red star, Chinese PLAAF, North Korean KPAF; **BLUFOR**: neutral (default), US star-and-bar, South Korean
  ROKAF taegeuk, a generic UN-style roundel. Only designs free of copyright are shipped (state insignia, drawn by us as simple shapes). The
  default stays neutral: North Korean symbols are sensitive in South Korea, and the war's main air forces were Soviet, Chinese and American,
  so a fixed national pair would be both touchy and inaccurate.
- Files live in `src/il2ks/web/static/il2ks/img/<group>/<name>.svg`; server owners can replace any of them through `custom/static/`
  (TD-25). The names below are the contract: keep them.

**Placeholders now** `[DECIDED]` (maintainer, 2026-10-03, OQ-37): UI icons come from **Tabler Icons** (MIT, outline, 24 px grid,
`currentColor`; credited in `NOTICE`) until the designer delivers; aircraft silhouettes, artillery, AA, the logo mark and the header texture
stay our own drawings. Paid stock marketplaces don't fit an open-source package (doc 16).

## Asset list

The sections below explain each group; the **complete, generated file list** (every file with its page, size, placeholder source and release status) is the last section of this document.

Priority: **P1** = the site looks unfinished without it, **P2** = clearly better with it, **P3** = nice to have / later features.

### Brand (`brand/`)
| File | What | Size | Prio |
|---|---|---|---|
| `logo-mark.svg` | Default il2ks mark (e.g. a small aircraft silhouette with a roundel-like frame), used when a server hasn't uploaded its own logo | ~40 px high in the header, must scale to 512 | P1 |
| `favicon.svg` (+ PNG 32, 180 apple-touch, 512) | Browser tab icon, derived from the mark | 16–512 | P1 |
| `og-default.png` | Link preview image when a sortie or profile is shared on Discord or social media | 1200 × 630 | P1 |

### Aircraft (`aircraft/`)
Shown next to the aircraft name in tables (small icon) and in the header of a sortie and the per-aircraft table of a profile.
- **Small icon** (top-down silhouette, 24 px grid, `currentColor`): `mig-15bis.svg`, `f-86a-5.svg`, `f-80c-10.svg`, `f-84e.svg`,
  `f-51d.svg`, `yak-9p.svg`, `la-11.svg`, `il-10.svg` (the 8 playable types, P2), `generic-jet.svg`, `generic-prop.svg`, `unknown.svg`
  (fallbacks, P1), AI types `b-29.svg`, `tu-2.svg`, `c-47b.svg`, `li-2.svg` (P3). The file name is the slug of the game's log name (lower case, non-alphanumerics to `-`).
- **Side profile** (`<type>-profile.svg`, about 480 × 160, may use 2–3 tones that follow the theme): the 8 playable types for the sortie
  page header (P3).
- New playable aircraft arrive with game updates (a bomber expansion is announced), so the set will grow; the site falls back to the
  generic jet / prop icon when a file is missing.

### Coalitions (`coalition/`)
| File | What | Prio |
|---|---|---|
| `redfor.svg`, `blufor.svg` | Neutral emblems for the two sides (communist side / UN side), used in badges next to "REDFOR" / "BLUFOR" and on the mission page | P2 |
| `coalition/insignia/<name>.svg` | The optional insignia: `vvs`, `plaaf`, `kpaf`, `usaf`, `rokaf`, `un` (simple redrawn state insignia; placeholders drawn by us) | P2 |
| `country/<code>.svg` | Later: real period insignia per country code (501–503, 601–603), once the codes are mapped to nations | P3 |

### Sortie outcome and pilot fate (`outcome/`)
Small status icons used in badges in sortie tables, on the sortie page, and on the profile. P1 for the first eight.
`landed`, `ditched` (landed away from an airfield), `crashed`, `shot-down`, `in-flight`, `not-taken-off`, `mission-ended`, `unknown`,
`bailed-out` (parachute), `exited-on-ground`, `disconnected` (left the server), `captured`, `wounded`, `dead`.

### Timeline events (`event/`)
The sortie page lists the flight as a timeline; each row starts with an icon. P2.
`spawn`, `takeoff`, `landing`, `kill-air`, `kill-ground`, `assist`, `friendly-fire`, `damaged`, `destroyed` (own aircraft lost),
`bailout`, `disconnect`, `sortie-end`, `bomb-release`, `rocket-salvo`.

### Ground targets (`ground/`)
Used in the collapsible ground-kill breakdown (profile and sortie page). P2.
`tank`, `vehicle` (trucks, cars), `artillery`, `aaa` (anti-aircraft guns), `ship`, `train`, `building`, `parked-aircraft`, `other-static`
(fences, crates, stacks). Final list follows the catalog categories (FR-WEB-4).

### Combat role (`role/`)
`air-superiority` (guns only), `attack` (bombs / rockets / napalm). Small badges on sorties. P2.

### Stat tiles (`stat/`)
Summary tiles on the player profile (big number + label + icon). P2.
`sorties`, `flight-time`, `air-kills`, `ground-kills`, `assists`, `deaths`, `planes-lost`, `bailouts`, `captures`, and two deliberately
**humorous** "hall of shame" ones: `taxi-accidents` (crashed before even taking off) and `strafed` (destroyed while parked on the ground), plus `friendly-fire` (hall of shame).
Later: `elo-prop`, `elo-jet` (ratings, P3).

### Textures and illustrations (`pattern/`, `illustration/`)
| File | What | Prio |
|---|---|---|
| `pattern/camo.svg` | Tileable, very low-contrast camo or topographic-map texture for the header band only | P2 |
| `illustration/404.svg` | "Page not found" (e.g. a pilot drifting under a parachute) | P2 |
| `illustration/500.svg` | "Something broke" (e.g. an engine trailing smoke) | P3 |
| `illustration/empty.svg` | Empty states: no search results, a mission nobody flew, a player with no sorties | P2 |
| `illustration/loading.svg` | Small loading indicator for live search and table updates (e.g. a spinning propeller) | P3 |

### Later features (not needed for the first release)
Medals and awards (FR-WEB-11), rank insignia, tour banners (it2), a map style for a future sortie map (FR-WEB-12).

<!-- inventory:start (generated by `uv run il2ks dev assets --write`; do not edit by hand) -->

## Complete file inventory

Generated from the code by `uv run il2ks dev assets --write`; a unit test fails when it drifts from the templates, the Python icon maps, the CSS or the files in `static/il2ks/img/`. Paths are relative to `src/il2ks/web/static/il2ks/img/`. **Release**: *yes* = the page needs the file (a placeholder is enough), *no* = optional or later. **State**: *shipped* = a placeholder file exists, *unused* = shipped but no page uses it yet, *planned* = not drawn and not (fully) wired yet; the site works without it. The uploaded server logo is not a static file: it is re-encoded to PNG (at most 256 px high) and served from `/media/branding/`.

Total: 100 files in 11 groups; 75 shipped as placeholders (3 of them not used by any page yet), 25 planned.

### Brand (`brand/`): 6 files (2 shipped)

| File | Used in | Size / format | Placeholder source | Release | Prio | State |
|---|---|---|---|---|---|---|
| `brand/logo-mark.svg` | Header (about 40 px high) when no logo is uploaded; home page hero, faint | SVG, viewBox 64, scales to 512 | own drawing | yes | P1 | shipped |
| `brand/favicon.svg` | Browser tab icon (`base.html` `<link rel=icon>`) | SVG, viewBox 64 | own drawing | yes | P1 | shipped |
| `brand/favicon-32.png` | Fallback tab icon for browsers without SVG favicons (not wired yet) | PNG 32 x 32 | own drawing | no | P1 | planned |
| `brand/apple-touch-icon.png` | iOS home-screen icon (not wired yet) | PNG 180 x 180 | own drawing | no | P1 | planned |
| `brand/icon-512.png` | Web-app / large icon (not wired yet) | PNG 512 x 512 | own drawing | no | P2 | planned |
| `brand/og-default.png` | Link preview (`og:image`) when a sortie is shared; the tag is added once the file exists | PNG 1200 x 630 | own drawing | no | P1 | missing |

### Aircraft (`aircraft/`): 23 files (3 shipped)

| File | Used in | Size / format | Placeholder source | Release | Prio | State |
|---|---|---|---|---|---|---|
| `aircraft/generic-jet.svg` | Fallback for a jet without its own file (`aircraft_icon` tag): tables, sortie header, aircraft pages | SVG 24 x 24, currentColor | own drawing | yes | P1 | shipped |
| `aircraft/generic-prop.svg` | Fallback for a propeller aircraft without its own file: tables, sortie header, aircraft pages | SVG 24 x 24, currentColor | own drawing | yes | P1 | shipped |
| `aircraft/unknown.svg` | Fallback when the propulsion is unknown: tables, sortie header, aircraft pages | SVG 24 x 24, currentColor | own drawing | yes | P1 | shipped |
| `aircraft/f-51d.svg` | Own icon of a playable type, picked by log-name slug (`f-51d`) | SVG 24 x 24, currentColor | own drawing | no | P2 | planned |
| `aircraft/f-80c-10.svg` | Own icon of a playable type, picked by log-name slug (`f-80c-10`) | SVG 24 x 24, currentColor | own drawing | no | P2 | planned |
| `aircraft/f-84e.svg` | Own icon of a playable type, picked by log-name slug (`f-84e`) | SVG 24 x 24, currentColor | own drawing | no | P2 | planned |
| `aircraft/f-86a-5.svg` | Own icon of a playable type, picked by log-name slug (`f-86a-5`) | SVG 24 x 24, currentColor | own drawing | no | P2 | planned |
| `aircraft/il-10.svg` | Own icon of a playable type, picked by log-name slug (`il-10`) | SVG 24 x 24, currentColor | own drawing | no | P2 | planned |
| `aircraft/la-11.svg` | Own icon of a playable type, picked by log-name slug (`la-11`) | SVG 24 x 24, currentColor | own drawing | no | P2 | planned |
| `aircraft/mig-15bis.svg` | Own icon of a playable type, picked by log-name slug (`mig-15bis`) | SVG 24 x 24, currentColor | own drawing | no | P2 | planned |
| `aircraft/yak-9p.svg` | Own icon of a playable type, picked by log-name slug (`yak-9p`) | SVG 24 x 24, currentColor | own drawing | no | P2 | planned |
| `aircraft/b-29.svg` | Own icon of an AI-only type (`b-29`), e.g. in the sortie timeline | SVG 24 x 24, currentColor | own drawing | no | P3 | planned |
| `aircraft/c-47b.svg` | Own icon of an AI-only type (`c-47b`), e.g. in the sortie timeline | SVG 24 x 24, currentColor | own drawing | no | P3 | planned |
| `aircraft/li-2.svg` | Own icon of an AI-only type (`li-2`), e.g. in the sortie timeline | SVG 24 x 24, currentColor | own drawing | no | P3 | planned |
| `aircraft/tu-2.svg` | Own icon of an AI-only type (`tu-2`), e.g. in the sortie timeline | SVG 24 x 24, currentColor | own drawing | no | P3 | planned |
| `aircraft/f-51d-profile.svg` | Side profile of `f-51d` for the sortie page header (not wired yet) | SVG, about 480 x 160, 2-3 tones | own drawing | no | P3 | planned |
| `aircraft/f-80c-10-profile.svg` | Side profile of `f-80c-10` for the sortie page header (not wired yet) | SVG, about 480 x 160, 2-3 tones | own drawing | no | P3 | planned |
| `aircraft/f-84e-profile.svg` | Side profile of `f-84e` for the sortie page header (not wired yet) | SVG, about 480 x 160, 2-3 tones | own drawing | no | P3 | planned |
| `aircraft/f-86a-5-profile.svg` | Side profile of `f-86a-5` for the sortie page header (not wired yet) | SVG, about 480 x 160, 2-3 tones | own drawing | no | P3 | planned |
| `aircraft/il-10-profile.svg` | Side profile of `il-10` for the sortie page header (not wired yet) | SVG, about 480 x 160, 2-3 tones | own drawing | no | P3 | planned |
| `aircraft/la-11-profile.svg` | Side profile of `la-11` for the sortie page header (not wired yet) | SVG, about 480 x 160, 2-3 tones | own drawing | no | P3 | planned |
| `aircraft/mig-15bis-profile.svg` | Side profile of `mig-15bis` for the sortie page header (not wired yet) | SVG, about 480 x 160, 2-3 tones | own drawing | no | P3 | planned |
| `aircraft/yak-9p-profile.svg` | Side profile of `yak-9p` for the sortie page header (not wired yet) | SVG, about 480 x 160, 2-3 tones | own drawing | no | P3 | planned |

### Coalitions (`coalition/`): 8 files (8 shipped)

| File | Used in | Size / format | Placeholder source | Release | Prio | State |
|---|---|---|---|---|---|---|
| `coalition/redfor.svg` | Neutral REDFOR emblem: side badges, tables, mission page (`coalition_icon` tag) | SVG 24 x 24, currentColor | Tabler `star` | yes | P2 | shipped |
| `coalition/blufor.svg` | Neutral BLUFOR emblem: side badges, tables, mission page (`coalition_icon` tag) | SVG 24 x 24, currentColor | Tabler `shield` | yes | P2 | shipped |
| `coalition/insignia/vvs.svg` | Optional insignia `vvs`, chosen in the site settings, replaces the neutral emblem | SVG 24 x 24, currentColor (own colours allowed) | own drawing | no | P2 | shipped |
| `coalition/insignia/plaaf.svg` | Optional insignia `plaaf`, chosen in the site settings, replaces the neutral emblem | SVG 24 x 24, currentColor (own colours allowed) | own drawing | no | P2 | shipped |
| `coalition/insignia/kpaf.svg` | Optional insignia `kpaf`, chosen in the site settings, replaces the neutral emblem | SVG 24 x 24, currentColor (own colours allowed) | own drawing | no | P2 | shipped |
| `coalition/insignia/usaf.svg` | Optional insignia `usaf`, chosen in the site settings, replaces the neutral emblem | SVG 24 x 24, currentColor (own colours allowed) | own drawing | no | P2 | shipped |
| `coalition/insignia/rokaf.svg` | Optional insignia `rokaf`, chosen in the site settings, replaces the neutral emblem | SVG 24 x 24, currentColor (own colours allowed) | own drawing | no | P2 | shipped |
| `coalition/insignia/un.svg` | Optional insignia `un`, chosen in the site settings, replaces the neutral emblem | SVG 24 x 24, currentColor (own colours allowed) | own drawing | no | P2 | shipped |

### Sortie outcome and pilot fate (`outcome/`): 14 files (14 shipped)

| File | Used in | Size / format | Placeholder source | Release | Prio | State |
|---|---|---|---|---|---|---|
| `outcome/bailed-out.svg` | Badge: pilot fate `bailed_out` (sortie tables, sortie page, profile) | SVG 24 x 24, currentColor | Tabler `parachute` | yes | P1 | shipped |
| `outcome/captured.svg` | Badge: pilot status `captured` (sortie tables, sortie page, profile) | SVG 24 x 24, currentColor | Tabler `prison` | yes | P1 | shipped |
| `outcome/crashed.svg` | Badge: sortie outcome `crashed` (sortie tables, sortie page, profile) | SVG 24 x 24, currentColor | Tabler `flame` | yes | P1 | shipped |
| `outcome/dead.svg` | Badge: pilot status `dead`; also the timeline rows `killed` and `died` (sortie tables, sortie page, profile) | SVG 24 x 24, currentColor | Tabler `cross` | yes | P1 | shipped |
| `outcome/disconnected.svg` | Badge: pilot fate `disconnected` (sortie tables, sortie page, profile) | SVG 24 x 24, currentColor | Tabler `plug-connected-x` | yes | P1 | shipped |
| `outcome/ditched.svg` | Badge: sortie outcome `ditched` (sortie tables, sortie page, profile) | SVG 24 x 24, currentColor | Tabler `ripple` | yes | P1 | shipped |
| `outcome/exited-on-ground.svg` | Badge: pilot fate `exited_on_ground` (sortie tables, sortie page, profile) | SVG 24 x 24, currentColor | Tabler `logout` | yes | P1 | shipped |
| `outcome/in-flight.svg` | Badge: sortie outcome `in_flight`; sortie outcome `airborne` (sortie tables, sortie page, profile) | SVG 24 x 24, currentColor | Tabler `plane-inflight` | yes | P1 | shipped |
| `outcome/landed.svg` | Badge: sortie outcome `landed` (sortie tables, sortie page, profile) | SVG 24 x 24, currentColor | Tabler `plane-arrival` | yes | P1 | shipped |
| `outcome/mission-ended.svg` | Badge: listed in the brief, no outcome value uses it yet (sortie tables, sortie page, profile) | SVG 24 x 24, currentColor | Tabler `flag-check` | no | P1 | unused |
| `outcome/not-taken-off.svg` | Badge: sortie outcome `not_taken_off` (sortie tables, sortie page, profile) | SVG 24 x 24, currentColor | Tabler `parking` | yes | P1 | shipped |
| `outcome/shot-down.svg` | Badge: sortie outcome `shot_down` (sortie tables, sortie page, profile) | SVG 24 x 24, currentColor | Tabler `plane-off` | yes | P1 | shipped |
| `outcome/unknown.svg` | Badge: sortie outcome `unknown`; pilot fate `unknown` (sortie tables, sortie page, profile) | SVG 24 x 24, currentColor | Tabler `help` | yes | P1 | shipped |
| `outcome/wounded.svg` | Badge: pilot status `wounded` (sortie tables, sortie page, profile) | SVG 24 x 24, currentColor | Tabler `bandage` | yes | P1 | shipped |

### Timeline events (`event/`): 14 files (14 shipped)

| File | Used in | Size / format | Placeholder source | Release | Prio | State |
|---|---|---|---|---|---|---|
| `event/assist.svg` | Sortie page timeline row | SVG 24 x 24, currentColor | Tabler `users` | yes | P2 | shipped |
| `event/bailout.svg` | Sortie page timeline row | SVG 24 x 24, currentColor | Tabler `parachute` | yes | P2 | shipped |
| `event/bomb-release.svg` | Sortie page timeline row | SVG 24 x 24, currentColor | Tabler `bomb` | yes | P2 | shipped |
| `event/damaged.svg` | Sortie page timeline row | SVG 24 x 24, currentColor | Tabler `bolt-off` | yes | P2 | shipped |
| `event/destroyed.svg` | Sortie page timeline row | SVG 24 x 24, currentColor | Tabler `flame` | yes | P2 | shipped |
| `event/disconnect.svg` | Sortie page timeline row | SVG 24 x 24, currentColor | Tabler `plug-connected-x` | yes | P2 | shipped |
| `event/friendly-fire.svg` | Sortie page timeline row | SVG 24 x 24, currentColor | Tabler `alert-octagon` | yes | P2 | shipped |
| `event/kill-air.svg` | Sortie page timeline row | SVG 24 x 24, currentColor | Tabler `crosshair` | yes | P2 | shipped |
| `event/kill-ground.svg` | Sortie page timeline row | SVG 24 x 24, currentColor | Tabler `tank` | yes | P2 | shipped |
| `event/landing.svg` | Sortie page timeline row | SVG 24 x 24, currentColor | Tabler `plane-arrival` | yes | P2 | shipped |
| `event/rocket-salvo.svg` | Sortie page timeline row | SVG 24 x 24, currentColor | Tabler `rocket` | yes | P2 | shipped |
| `event/sortie-end.svg` | Sortie page timeline row | SVG 24 x 24, currentColor | Tabler `flag-check` | yes | P2 | shipped |
| `event/spawn.svg` | Sortie page timeline row | SVG 24 x 24, currentColor | Tabler `login` | yes | P2 | shipped |
| `event/takeoff.svg` | Sortie page timeline row | SVG 24 x 24, currentColor | Tabler `plane-departure` | yes | P2 | shipped |

### Ground targets (`ground/`): 9 files (9 shipped)

| File | Used in | Size / format | Placeholder source | Release | Prio | State |
|---|---|---|---|---|---|---|
| `ground/tank.svg` | Ground-kill breakdown (profile, sortie page): category `tank`; also timeline rows | SVG 24 x 24, currentColor | Tabler `tank` | yes | P2 | shipped |
| `ground/vehicle.svg` | Ground-kill breakdown (profile, sortie page): category `vehicle`; also timeline rows | SVG 24 x 24, currentColor | Tabler `truck` | yes | P2 | shipped |
| `ground/artillery.svg` | Ground-kill breakdown (profile, sortie page): category `artillery`; also timeline rows | SVG 24 x 24, currentColor | own drawing | yes | P2 | shipped |
| `ground/aaa.svg` | Ground-kill breakdown (profile, sortie page): category `aaa`; also timeline rows | SVG 24 x 24, currentColor | own drawing | yes | P2 | shipped |
| `ground/ship.svg` | Ground-kill breakdown (profile, sortie page): category `ship`; also timeline rows | SVG 24 x 24, currentColor | Tabler `ship` | yes | P2 | shipped |
| `ground/train.svg` | Ground-kill breakdown (profile, sortie page): category `train`; also timeline rows | SVG 24 x 24, currentColor | Tabler `train` | yes | P2 | shipped |
| `ground/building.svg` | Ground-kill breakdown (profile, sortie page): category `building`; also timeline rows | SVG 24 x 24, currentColor | Tabler `building` | yes | P2 | shipped |
| `ground/parked-aircraft.svg` | Ground-kill breakdown (profile, sortie page): category `parked_aircraft`; also timeline rows | SVG 24 x 24, currentColor | Tabler `plane` | yes | P2 | shipped |
| `ground/other-static.svg` | Ground-kill breakdown (profile, sortie page): category `other`; also timeline rows | SVG 24 x 24, currentColor | Tabler `packages` | yes | P2 | shipped |

### Combat role (`role/`): 2 files (2 shipped)

| File | Used in | Size / format | Placeholder source | Release | Prio | State |
|---|---|---|---|---|---|---|
| `role/air-superiority.svg` | Sortie role badge (guns only) | SVG 24 x 24, currentColor | Tabler `swords` | yes | P2 | shipped |
| `role/attack.svg` | Sortie role badge (bombs / rockets / napalm) | SVG 24 x 24, currentColor | Tabler `bomb` | yes | P2 | shipped |

### Stat tiles (`stat/`): 15 files (15 shipped)

| File | Used in | Size / format | Placeholder source | Release | Prio | State |
|---|---|---|---|---|---|---|
| `stat/sorties.svg` | Profile, aircraft, mission and home tiles (`stat_tile ... icon=`) | SVG 24 x 24, currentColor | Tabler `plane-departure` | yes | P2 | shipped |
| `stat/flight-time.svg` | Profile, aircraft and mission tiles (`stat_tile ... icon=`) | SVG 24 x 24, currentColor | Tabler `clock` | yes | P2 | shipped |
| `stat/air-kills.svg` | Profile, aircraft, mission and home tiles (`stat_tile ... icon=`) | SVG 24 x 24, currentColor | Tabler `crosshair` | yes | P2 | shipped |
| `stat/ground-kills.svg` | Profile, aircraft, mission and home tiles (`stat_tile ... icon=`) | SVG 24 x 24, currentColor | Tabler `tank` | yes | P2 | shipped |
| `stat/assists.svg` | Profile tile (`stat_tile ... icon=`) | SVG 24 x 24, currentColor | Tabler `users` | yes | P2 | shipped |
| `stat/deaths.svg` | Profile and aircraft tiles (`stat_tile ... icon=`) | SVG 24 x 24, currentColor | Tabler `skull` | yes | P2 | shipped |
| `stat/planes-lost.svg` | Profile and aircraft tiles (`stat_tile ... icon=`) | SVG 24 x 24, currentColor | Tabler `plane-tilt` | yes | P2 | shipped |
| `stat/bailouts.svg` | No tile yet (shipped, unused) (`stat_tile ... icon=`) | SVG 24 x 24, currentColor | Tabler `parachute` | no | P2 | unused |
| `stat/captures.svg` | No tile yet (shipped, unused) (`stat_tile ... icon=`) | SVG 24 x 24, currentColor | Tabler `prison` | no | P2 | unused |
| `stat/taxi-accidents.svg` | Profile hall of shame (humorous): crashed before taking off (`stat_tile ... icon=`) | SVG 24 x 24, currentColor | Tabler `car-crash` | yes | P2 | shipped |
| `stat/strafed.svg` | Profile hall of shame (humorous): destroyed while parked (`stat_tile ... icon=`) | SVG 24 x 24, currentColor | Tabler `target-arrow` | yes | P2 | shipped |
| `stat/friendly-fire.svg` | Profile hall of shame: sorties with a friendly kill (`stat_tile ... icon=`) | SVG 24 x 24, currentColor | own drawing | yes | P2 | shipped |
| `stat/elo-prop.svg` | Leaderboard switcher button (chess pieces for the Elo boards) | SVG 24 x 24, currentColor | Tabler `chess-queen` | yes | P2 | shipped |
| `stat/elo-jet.svg` | Leaderboard switcher button (chess pieces for the Elo boards) | SVG 24 x 24, currentColor | Tabler `chess-knight` | yes | P2 | shipped |
| `stat/interception.svg` | Leaderboard switcher button (chess pieces for the Elo boards) | SVG 24 x 24, currentColor | Tabler `radar-2` | yes | P2 | shipped |

### Navigation link icons (`nav/`): 4 files (4 shipped)

| File | Used in | Size / format | Placeholder source | Release | Prio | State |
|---|---|---|---|---|---|---|
| `nav/discord.svg` | Custom navigation links in the header (`NavLink.icon`) | SVG 24 x 24, currentColor | Tabler `brand-discord` | yes | P3 | shipped |
| `nav/forum.svg` | Custom navigation links in the header (`NavLink.icon`) | SVG 24 x 24, currentColor | Tabler `messages` | yes | P3 | shipped |
| `nav/patreon.svg` | Custom navigation links in the header (`NavLink.icon`) | SVG 24 x 24, currentColor | Tabler `brand-patreon` | yes | P3 | shipped |
| `nav/link.svg` | Custom navigation links in the header (`NavLink.icon`) | SVG 24 x 24, currentColor | Tabler `link` | yes | P3 | shipped |

### Textures (`pattern/`): 1 files (1 shipped)

| File | Used in | Size / format | Placeholder source | Release | Prio | State |
|---|---|---|---|---|---|---|
| `pattern/camo.svg` | Header band and home hero background (CSS `--il2-camo`), tiled, very low contrast | SVG 640 x 200 tile | own drawing | yes | P2 | shipped |

### Illustrations (`illustration/`): 4 files (3 shipped)

| File | Used in | Size / format | Placeholder source | Release | Prio | State |
|---|---|---|---|---|---|---|
| `illustration/404.svg` | Page-not-found page | SVG, viewBox 120 x 80, uses currentColor | own drawing | yes | P2 | shipped |
| `illustration/500.svg` | Server-error page | SVG, viewBox 120 x 80, uses currentColor | own drawing | yes | P3 | shipped |
| `illustration/empty.svg` | Empty table rows (`empty_row` component) | SVG, viewBox 120 x 80, uses currentColor | own drawing | yes | P2 | shipped |
| `illustration/loading.svg` | Loading indicator for live search and table updates (not wired yet) | SVG, small, animated or static | own drawing | no | P3 | planned |

<!-- inventory:end -->
