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
- Files live in `src/il2ks/web/static/il2ks/img/<group>/<name>.svg`; server owners can replace any of them through `custom/static/`
  (TD-25). The names below are the contract: keep them.

## Asset list

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
  (fallbacks, P1), AI types `b-29.svg`, `tu-2.svg`, `c-47b.svg`, `li-2.svg` (P3).
- **Side profile** (`<type>-profile.svg`, about 480 × 160, may use 2–3 tones that follow the theme): the 8 playable types for the sortie
  page header (P3).
- New playable aircraft arrive with game updates (a bomber expansion is announced), so the set will grow; the site falls back to the
  generic jet / prop icon when a file is missing.

### Coalitions (`coalition/`)
| File | What | Prio |
|---|---|---|
| `redfor.svg`, `blufor.svg` | Neutral emblems for the two sides (communist side / UN side), used in badges next to "REDFOR" / "BLUFOR" and on the mission page | P2 |
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
**humorous** "hall of shame" ones: `taxi-accidents` (crashed before even taking off) and `strafed` (destroyed while parked on the ground).
Later: `elo-prop`, `elo-jet` (ratings, P3).

### Textures and illustrations (`pattern/`, `illustration/`)
| File | What | Prio |
|---|---|---|
| `pattern/header.svg` | Tileable, very low-contrast camo or topographic-map texture for the header band only | P2 |
| `illustration/404.svg` | "Page not found" (e.g. a pilot drifting under a parachute) | P2 |
| `illustration/500.svg` | "Something broke" (e.g. an engine trailing smoke) | P3 |
| `illustration/empty.svg` | Empty states: no search results, a mission nobody flew, a player with no sorties | P2 |
| `illustration/loading.svg` | Small loading indicator for live search and table updates (e.g. a spinning propeller) | P3 |

### Later features (not needed for the first release)
Medals and awards (FR-WEB-11), rank insignia, tour banners (it2), a map style for a future sortie map (FR-WEB-12).

