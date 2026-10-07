# Rules, scoring and tours

How to change how il2ks counts things, and what to run afterwards. You do not need any of this to get started: the
defaults work. Every setting has a home in `il2ks.toml`. Your `il2ks.toml` (written by `il2ks setup` from the `il2ks.example.toml` that ships with il2ks) lists them all
with their defaults and a comment each. The **game rules** (points, penalties, tours, rams, leaderboard minimums, ...) can also be
changed in the web admin: see [Where a setting lives](#where-a-setting-lives).

Contents: [Where a setting lives](#where-a-setting-lives) · [The three steps](#the-three-steps) ·
[What to run after a change](#what-to-run-after-a-change) ·
[Rules](#rules) · [Score and leaderboards](#score-and-leaderboards) · [Tours](#tours) ·
[Other sections](#other-sections) · [Upgrading](#upgrading) · [The admin area](#the-admin-area)

## Where a setting lives

Settings are of two kinds:

- **Game rules** decide how il2ks counts: points, penalties, ratings, tours, rams, how a flight is read, the minimums for the
  boards. You can change them in the admin (**Scoring**, **Tours**, **Rules**, **Leaderboards**) without touching a file or
  restarting anything.
- **Machine settings** say where and how il2ks runs: folders, ports, domain, database, backups, the server's identity and
  time zone, the timers of the log watcher. They stay in `il2ks.toml` (a wrong one can stop il2ks from starting, or lock you
  out of the admin, and most need a restart).

| Section | Where | Admin page |
|---|---|---|
| `[score]` points per kill and penalties | admin | Scoring |
| `[score]` `min_sorties`, `min_elo_games`, `min_attack_sorties`, `min_time_on_target_minutes`, `min_air_superiority_sorties`, `min_air_superiority_minutes` | admin | Leaderboards |
| `[ratings]` (Elo: `start`, `k`, `cross_pool_weight`) | admin | Scoring |
| `[killboard]` `assists` | admin | Scoring |
| `[marks]` `min_sorties` | admin | Leaderboards |
| `[tours]` `mode`, `start`, `timezone` | admin | Tours |
| `[rules]` (`credit_rams`, `ram_window_s`, `ram_distance_m`) | admin | Rules |
| `[replay]` (the fine rules: bailout distance, time windows, assist threshold, ...) | admin | Rules |
| The flight-time score (not in the file) and "new tour after a decisive mission" (not in the file) | admin only | Scoring, Tours |
| `data_dir`, `log_level`, `log_keep_days`, `debug` | file only | |
| `[logs]`, `[ingest]` (folders, when a mission is finished, how often to look) | file only | |
| `[live]` (timers of the "online now" box; its on/off for visitors' sorties is an admin switch in Site settings) | file only | |
| `[backup]`, `[web]`, `[https]` | file only | |
| `[server]` `timezone`, `uid` (the server's identity: it decides when a mission started) | file only | |

**How an admin value relates to the file.** Every game-rule field in the admin starts empty and shows what the file says
(or the built-in default) as grey placeholder text. Empty means "use the file". Once you type a value and save, **your value
wins** over the file, until you empty the field again. Both go through the same checks, so a value that il2ks.toml would
refuse is refused in the admin too. `il2ks doctor` lists the rules set in the admin, so a changed line in the file that seems to do nothing is
explained. (Environment variables such as `IL2KS_SCORE_AIR_KILL_PVP` set the file's value; the admin still wins over them.)

**When an admin change takes hold.** Each field says which kind it is:

| Kind | Fields | What happens |
|---|---|---|
| Re-scores every sortie | `[score]` points and penalties, `[ratings]`, `[killboard] assists`, the flight-time score | Shown as *pending*. The log watcher applies all pending changes together within a minute or so (one re-score and rebuild), or `il2ks rebuild-aggregates` does. Until then every page shows the old numbers, and new missions use the old rules too, so nothing is ever mixed. |
| Moves missions into other tours | `[tours]`, "new tour after a decisive mission" | Shown as *pending*. The watcher reassigns the missions and rebuilds, or run `il2ks rebuild-aggregates --retour`. |
| New missions at once | `[rules]`, `[replay]` | Used by the next mission that is ingested. Older missions keep their numbers until **Reprocess all missions** (Ingestion status page) or `il2ks reprocess`. |
| Applies at once | The board minimums `min_sorties`, `min_attack_sorties`, `min_time_on_target_minutes`, `min_air_superiority_sorties`, `min_air_superiority_minutes`, and `[marks]` | The boards use them on the next page view; the Top 10%/25% marks are computed again when you save. |
| Recomputed by the next rebuild | `[score] min_elo_games` | Shown as *pending*, applied by the watcher's rebuild (or `il2ks rebuild-aggregates`) together with other pending changes. It re-scores nothing: it decides which tours the stored all-time Elo may come from (the best tour with at least that many games), and the Elo boards and Elo marks follow once it is applied. |

## The three steps

For game rules you can use the admin instead (see above). To change a machine setting, or if you prefer the file:

1. Edit `il2ks.toml`. (Windows installer: `C:\ProgramData\il2ks\il2ks.toml`, open it with Notepad as administrator.)
2. **Restart il2ks.** Settings are read when it starts.
3. Run the command from the table below, if there is one. Without it, old missions keep the old numbers.

A rule that is also set in the admin keeps the admin's value: the file's line only shows as the default there.

Run the commands where you run `il2ks backup`: with the Windows installer, in **il2ks command prompt** (Start menu); in
Docker, as `docker compose -f docker/compose.yaml exec il2ks il2ks ...`. `il2ks doctor` warns about settings that
il2ks ignores, for example a misspelled key.

## What to run after a change

| You changed | Run | Why |
|---|---|---|
| Anything in `[replay]` (bailout distance, time windows, assist threshold, resupply, ...) | `il2ks reprocess --all` | These rules decide what happened in a flight, so the missions are read again from the archived logs. |
| Anything in `[rules]` (rams) | `il2ks reprocess --all` | Same: kills and deaths change. |
| `[server] timezone` | `il2ks reprocess --all`, then `il2ks rebuild-aggregates --retour` | It decides when a mission started, so also which tour it is in. |
| Points and penalties in `[score]` | `il2ks rebuild-aggregates` | Only the totals change. Quick. |
| `[ratings]` (Elo) | `il2ks rebuild-aggregates` | Same. |
| `[marks]` (the "Top 10%" marks) | `il2ks rebuild-aggregates` | Same. |
| `[killboard] assists` | `il2ks rebuild-aggregates` | Same. |
| `[tours]`: mode, start date or time zone | `il2ks rebuild-aggregates --retour` | Moves the old missions into the new tours. |
| The board minimums in `[score]` (`min_sorties`, `min_attack_sorties`, ...) | nothing more | The boards use them at once; the marks are computed again when you save in the admin (from the file: at the next rebuild). |
| `[score] min_elo_games` | `il2ks rebuild-aggregates` | Picks the tours the all-time Elo comes from (the best tour with at least that many games), so the stored ratings, the Elo boards and the Elo marks change with the rebuild. |
| `[logs]`, `[ingest]`, `[live]`, `[backup]`, `[web]`, `[https]` | nothing more | Restart is enough. |
| Title, logo, colors, fonts, menu links, game object names | nothing, no restart | Done in the admin; shows at once. |

Good to know:

- `reprocess` must be told what to do: `--all` for every mission, or `--since 2026-09-01` and/or `--until 2026-09-30`
  for a range of days (the server's local days), or `--mission UID` for one. It can take a long time on a big archive.
- If another il2ks job holds the database (the log watcher is ingesting), the command stops with a message. Add
  `--wait 600` to wait up to ten minutes instead.
- `rebuild-aggregates` is quick. Both commands also refresh the ratings, achievements and tours.
- `reprocess` reads the archived mission logs. If you set `[logs] after_archive = "delete"` there is no archive to
  read. The default keeps them in the data folder.
- A changed rule only touches **new** missions until you reprocess. That is on purpose.
- Want to be careful? `il2ks backup` first.
- **Delete all data and reprocess** (Ingestion status page, administrators only; or `il2ks reprocess --all --wipe`) starts
  from scratch: it makes a backup (if that fails, nothing is deleted), deletes every mission, player, tour and statistic,
  and builds them again from the archived logs, so the tours are cut again from the current tour settings. Your admin
  settings stay: site texts and branding, game rules, achievement and flight-time settings, quips, navigation links,
  object and country names, hidden players and missions, manual tour names and boundaries. It asks you to type
  `DELETE` (the command asks for `delete all data`; `--yes` skips that). Nothing is deleted when a stored mission has
  no archive. The site keeps running, but shows little or nothing until the missions are back.

## Rules

```toml
[rules]
credit_rams = true     # two enemy aircraft collide: each pilot gets a kill (friends never do)
ram_window_s = 0.5     # the two losses must be this close in time ...
ram_distance_m = 15.0  # ... and this close in space (metres)
```

Set `credit_rams = false` if ramming should not earn a kill, then run `il2ks reprocess --all`.
A pilot killed while parachuting is always counted as a death.

The fine rules under `[replay]` (how far from the wreck a bailout must land, how long a disconnect looks suspicious,
how much damage earns an assist, ...) have sensible defaults. Change one only if you see wrong results, then run
`il2ks reprocess --all`.

## Score and leaderboards

Every flight earns an **air score** (kills and assists in the air) and a **ground score** (ground kills by kind: tank,
vehicle, artillery, anti-aircraft, ship, train, building, parked aircraft, other). The two are rated apart. How a
flight ended costs a **percentage** of its score:

```toml
[score]
air_kill_pvp = 10.0          # shooting down another player
air_kill_ai = 2.0            # shooting down an AI aircraft
air_assist = 3.0
ground_tank = 6.0            # more kinds: ground_vehicle, ground_artillery, ground_aaa, ground_ship, ...
penalty_death_pct = 80.0     # percent of the flight's score lost when the pilot died
penalty_capture_pct = 50.0
penalty_plane_lost_pct = 20.0
penalty_early_bailout = 5.0  # flat points, not percent
penalty_friendly_kill = 3.0
```

After a change run `il2ks rebuild-aggregates`.

The boards are: air score, ground score, attack proficiency, interception, tank busting, Elo (propeller and jet)
and play time. The same `[score]` section sets how much a pilot must have done to appear on a board, so one lucky
flight cannot top it: `min_sorties`, `min_elo_games`, `min_attack_sorties`, `min_time_on_target_minutes`,
`min_air_superiority_sorties` and `min_air_superiority_minutes`. Lower them on a small server.

Show assists in their own column of the killboard (they are never mixed into the kills):

```toml
[killboard]
assists = true     # default false; then run il2ks rebuild-aggregates
```

The "Top 25%", "Top 10%", "Top 5%" and "Top 1%" marks on a player page only compare pilots with enough flights:

```toml
[marks]
min_sorties = 20
```

## Tours

A tour is a period with its own stats. The site opens on the current tour; visitors can pick an older one or
all-time. Elo ratings always stay all-time.

```toml
[tours]
mode = "monthly"   # "monthly", "days:14" (blocks of 14 days counted from `start`) or "manual"
start = ""         # only for days:N: the first day of tour 1, as YYYY-MM-DD
timezone = ""      # where a tour begins and ends (midnight there); empty = the server's time zone
```

With `mode = "manual"` you start the next tour yourself: admin, **Tours**, **Start a new tour now**. In any mode you can
rename a tour there. After changing mode, start or time zone in the file: `il2ks rebuild-aggregates --retour`. In the admin
(**Tour options**) the same three settings are fields, and the log watcher moves the missions for you.

**New tour after a decisive mission.** On a dynamic campaign server it can take weeks until one side wins. In the admin,
**Tour options**, tick *Start a new tour when a mission is won by one side* (off by default). From then on, a mission that
ends with a win for one side ends the tour, and the next mission starts a new one (a monthly tour becomes "October 2026",
"October 2026 (2)", ...). Draws, and missions without a result, change nothing. The mode above still applies: a win only
cuts its tours into parts, so use `mode = "manual"` if only wins should start tours. The result is read from the mission
log when a mission is processed. The `watch` process
applies a changed option within a minute or so (or run `il2ks rebuild-aggregates --retour`).

## Other sections

- `[logs]`: the log folder, and what happens to the originals afterwards (`after_archive` = `move`, `keep` or
  `delete`). With `remote = true` (logs are copied in from another machine) a file counts as complete once it has
  stopped changing for `[ingest] stable_seconds`.
- `[ingest]`: when a mission counts as finished (`idle_minutes`) and how often to look (`watch_interval_s`).
- `[live]`: the "online now" box. `enabled = false` switches it off.
- `[backup]`: how many backups to keep and whether to make one a day.
- `[web]`, `[https]`: ports, domain, certificate. See the install guides.

Every setting can also be an environment variable: `[web] threads` is `IL2KS_WEB_THREADS`. Variables win over the file.

## Upgrading

Install the new version as your install guide says ([Windows installer](install-windows.md#upgrading),
[by hand](install.md#upgrading), [Docker](install-docker.md#day-to-day)). On the first start il2ks:

1. makes a **backup** in the `backups` folder of your data folder,
2. updates the database,
3. fills in what the new version needs from the data you already have (new boards, achievements, ...). On a big
   database this can take a few minutes. The site is not up until it is done.

Afterwards run `il2ks doctor`. It lists settings that il2ks ignores, and customized pages that changed.

**New rules do not rewrite old missions by themselves.** If the release notes say a rule changed (for example whether rams
earn a kill), run `il2ks reprocess --all` to apply it to the old missions too.

If an upgrade goes wrong, stop il2ks and run `il2ks restore <zip>` with the backup made before it. Restore refuses
while il2ks is running, unless you add `--force`.

## The admin area

Sign in at `/admin/`. Besides **Site settings** ([customizing.md](customizing.md)):

- **Players**: select players and choose *Hide selected players from public pages* (a cheater, a test account).
  *Show ... again* undoes it. Names and numbers cannot be edited: they come from the logs.
- **Missions**: hide a mission the same way (a test mission). Its numbers leave the totals.
- **Scoring**, **Tours** (tour options), **Rules**, **Leaderboards**: the game rules of `il2ks.toml` (see
  [Where a setting lives](#where-a-setting-lives)), and the optional score for flight time.
- **Tours** (list): rename tours; in manual mode, start the next one.
- **Game objects**: change the shown name of a vehicle, building or aircraft. This name shows in every language.
  *Reset display names to the catalog defaults* undoes it.
- **Ingest runs**: what the log watcher did, and which mission failed, if any.

Create another admin or reset a password with `il2ks createadmin`.
