"""Vulture whitelist: code that looks unused to a static scan but must stay (C1; `uv run vulture`).

Every entry says what uses it. Remove an entry when the thing gets a real caller, or delete the thing. Not imported
anywhere and not type-checked: vulture only counts the names. `_` is a stand-in for "some object".
"""

# --- Django / framework glue: found by name at runtime -----------------------------------------------------------
DbConfig  # INSTALLED_APPS entry (il2ks.db.apps)
WebConfig  # INSTALLED_APPS entry (il2ks.web.apps)
_.default_auto_field  # Django AppConfig option
urlpatterns  # Django URLconf (il2ks.urls, il2ks.web.urls)
_.app_name  # Django URLconf namespace
application  # WSGI entry point (il2ks.wsgi, used by granian/gunicorn)
_.format  # logging.Formatter override (logsetup.JsonFormatter)
exc_type  # context manager protocol (__exit__ signature, ingest.lock)
tb  # context manager protocol (__exit__ signature, ingest.lock)

# --- ORM columns assigned on model instances in ingest.persist / runner / reprocess ------------------------------
_.finished_at  # IngestRun.finished_at, set when a run ends
_.players_total  # MissionStats columns (db.models)
_.sorties_total
_.redfor_sorties
_.blufor_sorties
_.damage_breakdown  # Sortie columns (db.models)
_.name_at_time
_.spawned_at
_.took_off_at
_.landed_at
_.ended_at
_.air_start
_.pos_spawn_x
_.pos_spawn_y
_.pos_spawn_z
_.pos_x  # Kill / event position columns (db.models)
_.pos_y
_.pos_z

# --- stdlib attribute assignment ---------------------------------------------------------------------------------
_.compress_type  # zipfile.ZipInfo.compress_type, read by zipfile when writing (ingest.archive)

# --- parsed log fields kept for completeness (doc 12): event dataclass fields nobody reads yet ------------------
_.mission_id  # AType 0 MID
_.mods  # AType 0 MODS
_.preset  # AType 0 PRESET
_.aqm_id  # AType 0 AQMID
_.icon_type  # AType 8 ICTYPE
_.form  # AType 10 FORM
_.is_player  # AType 10 ISPL
_.is_tstart  # AType 10 ISTSTART
_.group_id  # AType 11 GID (groups feed the later squadron/formation stats)
_.member_ids  # AType 11 IDS
_.leader_id  # AType 11 LID
_.bc  # AType 13 BC
_.store_id  # AType 25 TID (store-release tracking, planned with ordnance stats)
_.rocket_id  # AType 26 TID (rocket tracking, planned with ordnance stats)
_.editor_name  # core.catalog PayloadInfo.editor_name: payloads.csv column, kept for the payload display

# --- public API used by tests (and by the dev tools / future callers) --------------------------------------------
parse_line  # core.logparse.parser: single-line parse; production uses parse_lines; tests use it for per-line cases
FAKE_NAME_RE  # devtools.anonymize: the fixture check in tests/unit/test_anonymize.py matches names against it

# --- planned, no caller yet --------------------------------------------------------------------------------------
_.snapshot  # core.replay.state.Replay.snapshot: FR-ING-15 live sorties (streaming), tested in test_streaming.py
_.parent_sortie_index  # core.replay.result.SortieResult: gunner -> pilot link, not persisted yet
_.victim_object_id  # core.replay.result.KillResult: kept for a later per-victim view (tests set it)
