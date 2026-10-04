# Icons and images

The file names are a contract (design_doc/15): server owners replace any file through `custom/static/il2ks/img/...`.

## Tabler Icons (MIT)

UI icons are **Tabler Icons 3.48.0**, outline set (`@tabler/icons`, https://github.com/tabler/tabler-icons), MIT,
Copyright (c) 2020-2026 Pawel Kuna; the licence text is in `NOTICE`. Files are normalised: `viewBox="0 0 24 24"`,
`fill="none"`, `stroke="currentColor"`, stroke width 2, round caps and joins; the `width`, `height`, `class` attributes and
the empty bounding path are removed (the `{% icon %}` tag adds its own class). Source:
`https://cdn.jsdelivr.net/npm/@tabler/icons@3.48.0/icons/outline/<name>.svg`.

| File | Tabler icon |
|---|---|
| `event/spawn.svg` | `login` |
| `event/takeoff.svg` | `plane-departure` |
| `event/landing.svg` | `plane-arrival` |
| `event/kill-air.svg` | `crosshair` |
| `event/kill-ground.svg` | `tank` |
| `event/assist.svg` | `users` |
| `event/friendly-fire.svg` | `alert-octagon` |
| `event/damaged.svg` | `bolt-off` |
| `event/destroyed.svg` | `flame` |
| `event/bailout.svg` | `parachute` |
| `event/disconnect.svg` | `plug-connected-x` |
| `event/sortie-end.svg` | `flag-check` |
| `event/bomb-release.svg` | `bomb` |
| `event/rocket-salvo.svg` | `rocket` |
| `ground/tank.svg` | `tank` |
| `ground/vehicle.svg` | `truck` |
| `ground/ship.svg` | `ship` |
| `ground/train.svg` | `train` |
| `ground/building.svg` | `building` |
| `ground/parked-aircraft.svg` | `plane` |
| `ground/other-static.svg` | `packages` |
| `outcome/landed.svg` | `plane-arrival` |
| `outcome/ditched.svg` | `ripple` |
| `outcome/crashed.svg` | `flame` |
| `outcome/shot-down.svg` | `plane-off` |
| `outcome/in-flight.svg` | `plane-inflight` |
| `outcome/not-taken-off.svg` | `parking` |
| `outcome/mission-ended.svg` | `flag-check` |
| `outcome/unknown.svg` | `help` |
| `outcome/bailed-out.svg` | `parachute` |
| `outcome/exited-on-ground.svg` | `logout` |
| `outcome/disconnected.svg` | `plug-connected-x` |
| `outcome/captured.svg` | `prison` |
| `outcome/wounded.svg` | `bandage` |
| `outcome/dead.svg` | `cross` |
| `role/air-superiority.svg` | `swords` |
| `role/attack.svg` | `bomb` |
| `stat/sorties.svg` | `plane-departure` |
| `stat/flight-time.svg` | `clock` |
| `stat/air-kills.svg` | `crosshair` |
| `stat/ground-kills.svg` | `tank` |
| `stat/assists.svg` | `users` |
| `stat/deaths.svg` | `skull` |
| `stat/planes-lost.svg` | `plane-tilt` |
| `stat/bailouts.svg` | `parachute` |
| `stat/captures.svg` | `prison` |
| `stat/taxi-accidents.svg` | `car-crash` |
| `stat/strafed.svg` | `target-arrow` |
| `stat/elo-jet.svg` | `chess-knight` |
| `stat/elo-prop.svg` | `chess-queen` |
| `stat/interception.svg` | `radar-2` |
| `coalition/redfor.svg` | `star` |
| `coalition/blufor.svg` | `shield` |
| `nav/discord.svg` | `brand-discord` |
| `nav/patreon.svg` | `brand-patreon` |
| `nav/forum.svg` | `messages` |
| `nav/link.svg` | `link` |

## Flags (MIT)

`flag/*.svg` (the footer language menu) are from **flag-icons 7.5.0** (https://github.com/lipis/flag-icons, `flags/4x3/`), MIT,
Copyright (c) 2013 Panayiotis Lipiridis; the licence text is in `NOTICE`. Source:
`https://cdn.jsdelivr.net/npm/flag-icons@7.5.0/flags/4x3/<code>.svg`. They are decorative (`alt=""`); the language's own
name is the label. `flag/es.svg` is reduced by hand to the plain red-yellow-red stripes (the coat of arms alone was 80 KB).

| File | Language | flag-icons code |
|---|---|---|
| `flag/us.svg` | English (the site's English is American English) | `us` |
| `flag/ru.svg` | Russian | `ru` |
| `flag/de.svg` | German | `de` |
| `flag/es.svg` | Spanish | `es` (reduced) |
| `flag/fr.svg` | French | `fr` |
| `flag/br.svg` | Brazilian Portuguese | `br` |

## Ours (original drawings, MIT like the rest of the project)

`aircraft/*` (silhouettes), `ground/artillery.svg`, `ground/aaa.svg`, `stat/friendly-fire.svg`, `brand/*` (logo mark, favicon), `coalition/insignia/*`,
`pattern/*`, `illustration/*`. The complete inventory (every file, where it is used, required for the release or not) is generated into design_doc/15 by `uv run il2ks dev assets --write`; `tests/unit/test_asset_inventory.py` fails when it drifts. A test (`tests/unit/test_icon_files.py`) checks that every icon named in a template or in
the code exists and that every icon is a well-formed 24 px `currentColor` SVG.
