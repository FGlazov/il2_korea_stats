# Pending decisions: ingest.persist and ingest.aggregates

Decisions the design docs didn't specify, taken during iteration 1. For the lead to fold into the docs.

- **Counted sorties = pilot sorties only.** Gunner sorties are stored on level 1 (`role=gunner`) but feed no counters
  (`PlayerMission`, `Player`, `PlayerAircraft`, mission counters), because gunner stats come later (FR-WEB-14). Why: avoids
  double counting a crew's flight time and kills. Code: `ingest/counters.py` `COUNTED_ROLES`.
- **One counter registry.** `SORTIE_COUNTERS` (ORM aggregates over `PlayerSortie`) is the only definition of the counters.
  `PlayerMission` and `PlayerAircraft` are built by grouping sorties with it; `Player` totals are sums of `PlayerMission`.
  A test checks it covers exactly the fields of `db.models.Counters`. Code: `ingest/counters.py`.
- **Incremental update = subtract old, add new.** On re-ingest, `subtract_mission` applies the old level-1 contribution as
  negative deltas (`F(field) - value`), the mission is rewritten, `add_mission` adds the new one. Float residue: players
  left with zero sorties get `flight_time_s` reset to 0 and empty `PlayerAircraft` rows are deleted (a rebuild wouldn't create
  them). Rows that still exist keep their PK (`save_mission` calls `subtract_mission(prune=False)` and prunes afterwards).
  Code: `ingest/aggregates.py`, `ingest/persist.py:save_mission`.
- **Identity fields are recomputed, not summed.** `Player.first_seen` = earliest sortie spawn, `last_seen` = latest sortie end,
  `current_name` = name on the latest spawn by time (ties: higher sortie PK), so an older mission ingested later never
  overwrites a newer name. `PlayerName` rows (per distinct name: first/last seen) are rebuilt from the sorties. Code:
  `aggregates.refresh_players`.
- **Players are never deleted.** A player whose only sorties disappear after a re-ingest keeps the row (URL stays stable,
  FR-WEB-13), with zero counters; identity fields keep their last values.
- **`GameObject` registration rule.** Created from `catalog.lookup()` (display name falls back to the log name when unknown).
  For existing rows `cls`, `is_playable` and `is_known` follow the catalog (so a catalog update fixes old unknowns), but
  `display_name` is only replaced while it still equals `log_name` (the auto-registered placeholder), so admin edits survive
  (TD-24). Registered types: `object_types_seen`, `unknown_object_types`, every sortie aircraft, and every kill victim/killer type.
  Code: `persist.register_game_objects`.
- **`Country` rows**: created when missing with `catalog.coalition_name(coalition)`; existing rows are never touched
  (admin-editable, FR-ADM-5). Code: `persist.register_countries`.
- **`Mission` columns.** `ended_at` = `started_at` + `end_tick`/50; `duration_s` = `end_tick`/50; `settings` is stored as
  `{"raw": <SETTINGS string>}` (the raw string isn't parsed yet); `countries` as `{"501": 1, ...}`; `is_hidden` is not touched
  on re-ingest. Code: `persist._mission_fields`.
- **Mission counters** (`sorties_total`, `redfor_sorties`, `blufor_sorties`, `kills_*`) count pilot sorties like the player
  totals; coalition 1 = redfor, 2 = blufor; `players_total` counts every player with a sortie of any role.
- **`PlayerMission`** exists for every player with a sortie of any role (counters 0 for a gunner-only player); coalition = that
  player's first sortie in the mission. Rows of players no longer in the mission are deleted on re-ingest.
- **`PlayerSortie`**: `air_start` = `spawn_type == "air"`; `payload_name` = `catalog.payload(type, payload_id).readable_name`
  (empty when unknown, truncated to 128); `ammo` json = `{loaded, left, hits}`; `damage_breakdown` and `timeline` entries link
  a counterpart player sortie by `sortie_id` (the DB PK, filled after the rows have PKs), positions as `[x, y, z]`, timeline
  entries also carry an ISO UTC `at`. Code: `persist._upsert_sorties` and the `_*_json` helpers.
- **`Kill` is upserted** by `(killer_sortie, victim_sortie, tick, credit)` (the unique constraint) so re-ingest keeps PKs and
  deletes the rest; only rows with both sortie indexes set are written. Code: `persist._replace_kills`.
- **Two sorties with the same `(account_uuid, spawn_tick)` in one result** are not handled: the unique constraint raises and
  the caller's transaction rolls back (surfaces as a failed `IngestRun`). The replay layer is expected not to produce them.
