"""Vulture whitelist: code that looks unused to a static scan but must stay (C1; `uv run vulture`).

Every entry says what uses it. Remove an entry when the thing gets a real caller, or delete the thing. Not imported
anywhere and not type-checked: vulture only counts the names. `_` is a stand-in for "some object".
"""

# --- Django / framework glue: found by name at runtime -----------------------------------------------------------
DbConfig  # INSTALLED_APPS entry (il2ks.db.apps)
WebConfig  # INSTALLED_APPS entry (il2ks.web.apps)
_.default_auto_field  # Django AppConfig option
_.verbose_name  # Django AppConfig option (db.apps)
urlpatterns  # Django URLconf (il2ks.urls, il2ks.web.urls)
AxesMiddleware  # MIDDLEWARE entry (il2ks.settings)
_.ready  # AppConfig hook (il2ks.web.apps connects the login log receivers)
lockout_response  # AXES_LOCKOUT_CALLABLE (il2ks.settings)
log_failed_login  # signal receiver (web.login_protection, @receiver)
log_lockout  # signal receiver (web.login_protection, @receiver)
sender  # signal receiver signature (web.login_protection)
trust_forwarded_for  # il2ks.settings (IL2KS_TRUST_FORWARDED_FOR; settings.py is not scanned)
fetch  # serving.outbound: the SSRF-guarded fetch has no caller yet; the polled Markdown source and webhooks will use it
parse_allow_list  # serving.outbound: turns `[outbound] allow_private` into networks for `fetch` (no caller yet)
_.app_name  # Django URLconf namespace
application  # WSGI entry point (il2ks.wsgi, used by granian/gunicorn)
_.format  # logging.Formatter override (logsetup.JsonFormatter)
exc_type  # context manager protocol (__exit__ signature, ingest.lock)
tb  # context manager protocol (__exit__ signature, ingest.lock)

# --- Django admin (web.admin, web.admin_site, web.admin_config, web.site_forms): options and hooks found by name ---
_.formset  # InlineModelAdmin option (NavLinkInline)
_.max_num  # InlineModelAdmin option
_.verbose_name_plural  # InlineModelAdmin option
_.inlines  # ModelAdmin option (SiteSettingsAdmin)
_.nav_links_help  # ModelAdmin readonly field (SiteSettingsAdmin.readonly_fields)
_.value_from_datadict  # Django Widget hook (site_forms.ThemeWidget)
_.attrs  # Django Widget.render signature (site_forms.ThemeWidget)
_.renderer  # Django Widget.render signature
_.widget  # Django Field option (site_forms.ThemeField)
_.to_python  # Django Field hook (site_forms.ThemeField)
_.theme_preset  # form field read by the admin form (site_forms.SiteSettingsForm.clean)
_.foreground  # web.theme.ContrastWarning: kept for the admin message and tests
_.background  # web.theme.ContrastWarning: same
Il2ksAdminConfig  # INSTALLED_APPS entry
_.default_site  # AdminConfig option
Il2ksAdminSite  # AdminConfig.default_site
_.index_template  # AdminSite option
SiteSettingsAdmin  # @admin.register
PlayerAdmin
MissionAdmin
GameObjectAdmin
CountryAdmin
IngestRunAdmin
TourAdmin
PlayerTourAdmin
_.list_display  # ModelAdmin options
_.list_filter
_.list_editable
_.list_display_links
_.search_fields
_.ordering
_.actions
_.fieldsets
_.readonly_fields
_.list_per_page
_.has_add_permission  # ModelAdmin permission hooks
_.has_delete_permission
_.has_change_permission
_.changelist_view  # SiteSettingsAdmin: a singleton has no list
extra_context  # changelist_view signature
_.current_logo  # readonly_fields display methods
_.uploaded_fonts
_.missions_count  # TourAdmin display column
_.start_view  # TourAdmin URL (admin:il2ks_db_tour_start)
_.change_list_template  # ModelAdmin option
_.list_select_related
_.get_urls  # ModelAdmin hook
_.warnings_count
_.error_text
_.warnings_list
_.unknown_atypes_table
_.unknown_keys_table
_.files_list
_.hide_selected  # admin actions
_.unhide_selected
_.reset_names
_.js  # forms.Media
_.widgets  # ModelForm.Meta
_.clean_accent_color  # ModelForm clean_<field> hooks
_.clean_links_text
_.clean_logo_upload
_.clean_favicon_upload
_.clean_home_bg_upload
_.clean_header_bg_upload
_.clean_font_upload
_.initial  # form field attribute
links_text  # declared form fields of SiteSettingsForm, listed in the admin fieldsets
logo_upload
remove_logo
favicon_upload  # branding pictures (web.branding_images), declared form fields
remove_favicon
header_bg_upload
remove_header_bg
home_bg_upload
remove_home_bg
font_upload
font_use
remove_fonts
Media
Meta

# --- il2ks.queries.tours: read helpers for the tour selector, called by the page views once they are wired (TD-26) ---
current_tour
tour_options
tour_choice
player_tour
player_tour_aircraft
tour_leaderboard

# --- Django middleware / template-only data -----------------------------------------------------------------------
DataVersionCacheMiddleware  # MIDDLEWARE entry (web.caching)
process_view  # Django middleware hook, called with the resolved view (web.caching)
view_func  # middleware hook signature (web.caching)
view_args
view_kwargs
_.threshold  # web.medals.Tier / Medal: shown by the achievement templates (_medal.html, overview.html)
_.mission_hidden  # web.medals.Medal: read by the profile and sortie medal templates (no link to a hidden mission)
_.more_mixes  # web.views.aircraft.HitsToDestroy: the "show more mixes" fold of aircraft/detail.html
_.style  # web.medals.Medal: the ribbon stripe pattern (ribbon--sN class) read by _ribbon.html and the home feed
_.tier_range  # web.medals.Medal: one pip per tier, looped over by _ribbon.html and the home feed
_.player_name  # web.templatetags.il2ks_achievements.FeedItem: shown by achievements/home_feed.html
_.missions_stored  # web.ingest_status.IngestOverview: fields are read by the admin template il2ks_ingest_status.html
_.last_run
_.last_ok
_.ok_recent
_.failed_recent
_.idle_completions
_.unknown_objects

# --- serving (settings.py is not scanned, and dictConfig / Django find some names by string) -------------------------
csrf_trusted_origins  # il2ks.settings (CSRF_TRUSTED_ORIGINS)
security_settings  # il2ks.settings
staticfiles_backend  # il2ks.settings (STORAGES)
secret_key_for  # il2ks.settings (SECRET_KEY)
_.proxy_ssl_header  # SecuritySettings fields: each becomes one Django SECURE_* / *_COOKIE_* setting in il2ks.settings
_.ssl_redirect
_.hsts_include_subdomains
_.hsts_preload
_.session_cookie_secure
_.csrf_cookie_secure
_.session_cookie_httponly
_.csrf_cookie_httponly
_.content_type_nosniff
_.referrer_policy
LenientManifestStorage  # STORAGES["staticfiles"] in il2ks.settings (dotted path)
_.manifest_strict  # Django ManifestFilesMixin option
make_file_handler  # logging dictConfig "()" factory, referenced by dotted path (logsetup.dict_config)
make_json_formatter  # logging dictConfig "()" factory, referenced by dotted path (logsetup.dict_config)
signum  # signal handler signature (serving.procutil)
frame  # signal handler signature (serving.procutil)

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
_.baseFilename  # logging.FileHandler reads it when it opens the file (logsetup._DailyFileHandler)
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

# --- first-run setup page (web.setup_forms, serving.djsettings): read by Django / the template by name ----------------
_.clean_domain  # SetupForm clean_<field> hooks
_.clean_admin_username
_.clean_email
logs_choice  # declared SetupForm fields, rendered by hand in templates/il2ks/setup.html and read from cleaned_data
logs_custom
logs_allow_missing
admin_password2
_.choices  # ChoiceField.choices, set per request to the time zone list
redirect_exempt  # SecuritySettings -> SECURE_REDIRECT_EXEMPT in settings.py (excluded from the scan)

# --- public API used by tests (and by the dev tools / future callers) --------------------------------------------
parse_line  # core.logparse.parser: single-line parse; production uses parse_lines; tests use it for per-line cases
FAKE_NAME_RE  # devtools.anonymize: the fixture check in tests/unit/test_anonymize.py matches names against it

# --- planned, no caller yet --------------------------------------------------------------------------------------
_.parent_sortie_index  # core.replay.result.SortieResult: gunner -> pilot link, not persisted yet
_.victim_object_id  # core.replay.result.KillResult: kept for a later per-victim view (tests set it)
_.is_active  # django User flags, assigned by createadmin so a reset account is a working admin (ops.admin)
_.is_staff
_.is_superuser
_.deaths  # web.views.styleguide.FakeRow: read by getattr in the demo table's sort and by the style guide template
_.redfor  # queries.live.OnlineNow: read by online_now_body.html
_.blufor  # same
_.unassigned  # same (players online who haven't spawned, so have no side yet)
current_data_version  # db.site: the version alone, for ingest/admin code and tests (pages read it via web.caching)

# --- written to ReprocessRequest model fields, or read by the admin status template -----------------------------------
_.missions_total  # ingest.reprocess_requests: model fields assigned while a reprocess runs, shown on the status page
_.missions_ok
_.missions_failed
_.missions_missing
_.reprocess_last  # web.ingest_status.IngestOverview: read by templates/admin/il2ks_ingest_status.html
__call__  # ingest.reprocess_requests.ReprocessFn: a Protocol, `reprocess` and the test fakes satisfy it
_.matched_name  # queries.players.PlayerHit: read by the player search template ("also known as")
# --- web.sortie_view view models: fields read only by the sortie templates (il2ks/sorties/*) ---------------------
_.share  # GroundRow: the breakdown table
_.static_share  # GroundBreakdown: the accordion hint
_.used_unknown  # AmmoTable: notices.html and ammo.html
_.place  # TimelineRow: tooltip with the position
_.air_kills  # Detail: kills.html
_.damage_more  # Detail: damage.html
_.timeline_more  # Detail: timeline.html
_.timeline_events  # Detail: timeline.html
_.LimitFlags  # ctypes struct field written to the Windows job object (serving.procutil.kill_children_when_we_die)
pid_alive  # serving.procutil: liveness probe, tests only since the run lock replaced the PID check
# --- ammo breakdown reads for the sortie and aircraft pages (FR-WEB-18): the page agents call these ---------------
_.other_hit_lines  # queries.ammo.SortieAmmo
_.has_ordnance  # queries.ammo.SortieAmmo
_.average_hits  # queries.ammo.AmmoToDestroy: hits / kills, divided at read time (TD-22)
_.by_ammo  # queries.ammo.AircraftAmmo
sortie_ammo_by_pk  # queries.ammo
aircraft_ammo  # queries.ammo
all_aircraft_ammo  # queries.ammo
# --- ammo and PvE pages (FR-WEB-18, FR-WEB-21): fields read only by templates --------------------------------------
_.planes_lost  # web.pve.LossRow: players/detail_pve.html
_.direct  # sortie_view.OrdnanceView: sorties/parts/ammo.html
_.lost_to  # sortie_view.Detail: sorties/parts/summary.html
_.average  # web.views.aircraft.AircraftRow / AmmoHits: aircraft/list.html
_.survived  # web.views.aircraft.AircraftRow: aircraft/list.html
_.nemeses  # web.templatetags.il2ks_boards.Board: players/detail_killboard.html
# --- web.charts: chart fields read only by components/bar_chart.html ---
_.chart_id
_.tip
_.plot_left
_.plot_right
_.legend
_.table_head
_.air_points  # PlayerSortie model fields: set by ingest.scoring, summed by name in the score counters (ingest.counters)
_.ground_points
_.calibre  # core.catalog.loader.AmmoInfo: ammo.csv columns kept for later pages (grouping/sorting by calibre); tested
_.round_type  # core.catalog.loader.AmmoInfo: same
_.compute_ratings  # pure-function wrapper over compute_all_ratings, kept for the unit tests (pools only)
_.shame  # core.achievements.Achievement: hall-of-shame flag, read by the profile / medal views and tested
_.compress_level  # zipfile.ZipInfo: per-entry DEFLATE level, set in ingest.archive.write_archive
_.page_param  # web.views.missions.SideSorties: read by missions/detail.html (the side table's pagination parameter)
# --- front-page image (FR-ADM-2) ---
_.feature_status  # ModelAdmin readonly field (SiteSettingsAdmin.readonly_fields)
_.current_favicon  # ModelAdmin readonly fields of the branding pictures (SiteSettingsAdmin.readonly_fields)
_.current_header_bg
_.current_home_bg
_.small_url  # web.feature_image.HomeFeatureView: read by il2ks/home_feature.html
_.small_width
_.caption
_.alt
_.help_texts  # SiteSettingsForm.Meta (Django ModelForm option)
_.clean_home_feature  # Django form hook: SiteSettingsForm.clean_<field>
_.required  # Django form field attribute (SiteSettingsForm.__init__: home_feature is optional in a post)
_.loadouts  # queries.builds.AircraftBuild: read by players/detail_aircraft_build.html
weapon_mod_mask  # core.catalog.loader: inverse of weapon_mod_ids, for the aircraft page mod filter (next); tested
_.score_flight  # SiteSettings: saved by web.admin_site.score_view, read as JSON by ingest.flight_score
_.stale_hides  # web.admin_quips.SpotRow: read by admin/il2ks_quips.html
_.default_description  # web.admin_achievements.AchievementRow: read by admin/il2ks_achievements.html
_.default_thresholds
_.customised
_.changes_rows
_.all_time_factor  # web.admin_achievements.AchievementRow: read by admin/il2ks_achievements.html
_.tour_default_description  # web.admin_achievements.AchievementRow: read by admin/il2ks_achievements.html
_.sortable  # web.columns.Column: read by aircraft/list.html (a header without a sort link)
_.elo_text  # web.views.aircraft.AircraftRow: read by aircraft/list.html

# --- templates read these (web.views.players.StarTiles, players/detail_tiles.html) ---
_.show_attack  # star tiles: attack proficiency shown
_.elo_jet_enough  # star tiles: Elo (jet) reaches the board minimum
_.elo_prop_enough  # star tiles: Elo (prop) reaches the board minimum
_.attack_hour  # star tiles: attack proficiency value
_.is_ram  # Kill.is_ram: written at ingest, read by the sortie page through the stored timeline
_.ram_with  # sortie_view.Detail: read by sorties/parts/header.html
_.has_ram  # sortie_view.Detail: read by sorties/parts/header.html and timeline.html
FIRST_RELEASE_DONE  # read by .github/workflows/release.yml (refuses a v* tag while False); templateversions.py
LoginHandler  # settings.AXES_HANDLER: axes imports it by name (web.login_protection)
get_failures  # LoginHandler overrides axes's method: axes calls it
