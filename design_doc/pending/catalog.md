# Pending decisions: catalog, timeutil, logsetup (iteration 1)

Decisions the design docs did not specify. Move into the design docs when reviewed.

## core.catalog

- **Crew bots, parachutes, ejection seats and spotters get class `unknown` but `is_known=True`.** `ObjectClass` (contract, mirrors
  `db.models.ObjectClass`) has no crew/equipment class and the contract is frozen. They are in `objects.csv` (so the coverage test
  passes and `is_known` is true), with readable display names (`Pilot (USAF, jet)`, `MiG-15bis ejection seat`). Affected rows:
  `Bot*`, `B29_CrewGroup*`, `CParachute`, `ESeat_*`, `Spotter`, `VehicleTurret`, `VehicleRangefinderTurret`. A `crew` class would be a
  one-line contract change if the replay wants to tell them apart; `is_known=False` is reserved for types missing from the data.
  Code: `core/catalog/data/objects.csv`.
- **Lookups normalise names** (`canonical_type_name`): a trailing block suffix `[group,index]` is removed (`GAZ_63[64606,0]` ->
  `GAZ_63`) and `CParachute_<n>` becomes `CParachute`. Case is ignored for matching, original case kept for the stored `log_name`.
  Why: static block objects and parachutes carry per-instance suffixes in `TYPE:`. Code: `loader.canonical_type_name`.
- **Unknown type's display name is the canonical (suffix-stripped) log name**, not the raw string. Code: `Catalog.lookup`.
- **Turrets**: `Turret_*` and `Multiturret_*` are `gunner`. Only `Turret_IL10` is `is_playable` (the only one seen in AType 10);
  B-29 and Tu-2 turrets are not. `Tu-2` and both `B 29` / `B-29` spellings are `bomber`, not playable. Code: `objects.csv`.
- **Static copies of vehicles and ships** (`Parked <aircraft>`, `Box Car A`, `Tanker Ship A`, `GAZ_63`, `Willys_MB`, ...) are
  `static` (scenery blocks), while the AI-driven `GAZ-63`, `Willys MB`, `Tank Car`, ... are `vehicle`. Why: static blocks are the
  objects that `[g,i]` block suffixes are attached to.
- **Ordnance** = drop/wing-tip tanks, JATO boosters, napalm and rockets (the stores seen as AType 12 spawns in the samples). No
  bomb types appear as AType 12 spawns there.
- **Heavy artillery** (howitzers, ML-20) is `vehicle`, self-propelled guns (SU-76M, ISU-122, M7 Priest, M40 GMC) are `tank`,
  AA-capable armoured vehicles (M16/M19 MGMC) and all `Platform Car AA*` are `aaa`. Best guess; the class is only a default
  that the admin can edit (TD-24).
- **`payload_aliases.csv`** maps log aircraft names to `payloads.csv` vehicle keys (`F-86A-5` -> `f-86a`, `B 29` -> `b-29`).
  Aircraft without an alias are tried under their own case-insensitive name. Code: `Catalog.payload`.
- **Duplicate object names (case-insensitive) raise `ValueError` when building a `Catalog`**, to catch data errors early.
  Code: `Catalog.__init__`.
- **Payload coverage in the 210 sample missions is 99.3%** (spec: >= 99%). The sample test needs `sample_data/`.

## ingest.timeutil (TD-15)

- **Spring-forward gap**: the wall time is shifted forward by the gap (02:30 -> 03:30 for a 1 h gap) with a warning. Code:
  `resolve_mission_start`.
- **Fall-back hour with a hint**: the candidate closest to `hint_utc` wins, ties go to the earlier instant. A naive `hint_utc`
  is taken as UTC. Without a hint: earlier instant (fold=0) and a warning.
- **`parse_mission_uid`** is public (returns the naive local datetime) for reuse by ingest.

## logsetup (TD-27, NFR-OBS-1)

- JSON line fields: `time` (UTC, ISO 8601, milliseconds), `level`, `logger`, `message`, `process`, any `extra=` fields,
  `exception`, `stack`. Non-serialisable extras are stringified.
- Rotation: 10 MB x 5 backups. The log file is created lazily on the first record.
- Idempotent: calling again replaces the handlers installed earlier by this module (other handlers are left alone) and re-applies
  the level to the root logger. An unknown level name raises `ValueError`.
