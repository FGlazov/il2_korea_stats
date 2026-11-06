# Changelog

## Unreleased

### Changed

- **With more than one web worker, the page cache keeps pages in each worker's memory as well as in the shared files**
  (`[web] page_cache = "auto"` now means `tiered` there; one worker stays `memory`). A page is answered as fast as from
  memory: 4 workers served about 2,150 pages a second instead of 1,600.

## 0.7.2 (2026-11-06)

A speed-up for the website. Nothing to run after the upgrade, and the stats don't change.

### Added

- **The website keeps its list pages ready on the server**: the home page, missions, players, sorties, aircraft (and each
  aircraft's page) and the leaderboards. A cached page is answered about ten times as fast (about 60 to about 600 a second on
  one worker). New stats and anything you save in the admin (a notice, the theme, the title) still show on the next request,
  and a few seconds after an import the server renders the default pages in the background, so the first visitor gets them
  fast too. Player, sortie and mission pages and searches are not cached. `[web] page_cache` picks where pages are kept:
  `auto` (the default: in memory with one worker, as shared files with more), `memory`, `files`, `tiered` or `off`
  (docs/settings.md).
  The files go to `<data dir>/cache/pages` and are emptied at every start. If a page ever looks out of date, `page_cache =
  "off"` turns it off (and please report it).

## 0.7.1 (2026-11-05)

A speed-up for the website. Nothing to run after the upgrade, and the stats don't change.

### Changed

- **The website keeps its database connections open** for up to 10 minutes. Before, every request opened a new one, about
  2.4 ms each time. A browser revalidating a page it already has (the `304` answer) is now answered almost three times as fast
  (237 to 662 a second on one worker), and full pages about 13% faster. Each web thread keeps one connection, so the default of
  one worker with 4 threads holds 4.
- **Stop the website before `il2ks restore --force`**, as the restore already asks. On Linux a forced restore under a running
  site now leaves it showing the old data until you restart it. On Windows the restore fails cleanly and nothing changes.
- **Windows installer:** Caddy 2.11.9 (security and bug fixes; none of the breaking changes touch the il2ks setup).

## 0.7.0 (2026-11-03)

An accessibility release. Nothing to run after the upgrade, and the stats don't change.

### Changed

- **The admin warns when a custom accent colour makes keyboard focus hard to see.** The accent draws the outline that shows keyboard
  users where they are (a table row, a menu, a scrolling table). The contrast warning after you save a theme now also checks it, at
  3:1 against the page, the cards and a highlighted row. The built-in themes all pass.
- **Admin pages work better with a screen reader.** Each box in the navigation-links table and the notice's date and time boxes now
  have a name of their own ("Url, link 2"). Before, a screen reader announced them without one. The admin login page has a heading, and
  the admin's breadcrumb links are underlined, not told apart by colour alone.
- **Player profiles and the achievements pages fit a small phone** (320 px wide, or a desktop window zoomed to 400%). Before, the whole
  page scrolled sideways a little: the medal cards were too wide, and long section headings and their "All achievements →" links
  wouldn't wrap.
- **Wider automatic accessibility checks** (WCAG 2.1 AA) in the tests: the error pages, the admin pages and the streak history are
  checked too, and every page at 320 px wide.

### Templates changed (check your custom/ overrides)

- `il2ks/site.css`: v8.1 -> v8.2 and `il2ks/players.css`: v4.0 -> v4.1 (the phone layout above).
- `admin/base_site.html`: v1.0 -> v1.1 (loads the new `il2ks_admin/admin.css`). An override keeps working, without the underlined
  breadcrumbs.
- New: `admin/login.html` v1.0 and `il2ks_admin/admin.css` v1.0.

## 0.6.1 (2026-11-03)

Run `il2ks reprocess --all` after the upgrade, also if you already ran it for 0.6.0: the early bailout rule changed. Without it, only
the scores and medals change (at the next rebuild).

### Changed

- **A friendly ground kill costs 1 point, a friendly air kill still 3.** At most 5 friendly kills cost points in a sortie, the air kills
  first. The new value is `penalty_friendly_ground_kill` in the `[score]` section of `il2ks.toml`, and on the admin's game rules page.
- **An early bailout needs at most 5% damage** (was under 20%): a pilot who bails out of an aircraft with more damage is not penalised, as a
  pilot in a death spiral may leave early with little damage. In our sample of 210 missions, 20 of 239 suspected bailouts are cleared.
- **Sides are shown at once during the stats delay:** online now shows each player's side straight away, as the game does. Aircraft, flight
  times and kills are still delayed.
- **Surgical Strike's platinum tier is 2000 points per hour** (was 2500).
- **Smaller CSS and JavaScript downloads:** il2ks now installs `brotli`, so the static files are also stored Brotli-compressed and
  browsers download about 12% less than with gzip (the stylesheets and htmx the most). Nothing to configure.

### Templates changed (check your custom/ overrides)

- `components/online_now_body.html`: v1.2 -> v1.3 (the delay notice). An override keeps working, with the old wording.

## 0.6.0 (2026-11-03)

Run `il2ks reprocess --all` once after the upgrade (it needs your archived logs and rebuilds the aggregates too). It finds the botched
landings and the takeoff crashes in the missions you already have, splits their friendly-fire kills, applies the new bailout rule, fills
the new mission counters and awards the new medals. Until then the new figures read 0, and Jet Hunter medals drop away at the first
rebuild and return with the run.

### Added

- **Botched landings in the hall of shame:** a pilot who crashes or badly damages a sound aircraft landing at an airfield, by their
  own doing. On the profile, the aircraft and mission pages, and the sortie page with a badge and a quip of its own. The airfield
  distance and damage limits are in the `[replay]` section of `il2ks.toml` (`botched_landing_*`).
- **The hall of shame quips cover every combination** of taxi/takeoff accidents, friendly-fire air kills, friendly-fire ground kills
  and botched landings, each with its warmer variant for the pilots who do it most.
- **The sortie quips for friendly fire depend on what was hit:** a friendly aircraft, friendly ground targets, or both, each with
  lines of its own. Your choices for the old friendly-fire quips now apply to friendly aircraft.
- **Aircraft pages show more figures:** a plain section under the tiles with the ratios, the hall of shame and every other figure
  the list offers as a column.
- **Mission pages show the hall of shame incidents** and the other figures of the list in a plain section under the tiles.
- **Times strafed on the ground is now on aircraft and mission pages too**, as a plain total: it is no shame.
- The profile's "Other totals" show total assists and gun accuracy.
- **Reprocess a time span in the admin:** the Ingestion status page has a form for a start and an end, each a date with an optional time
  to the minute, and shows how many missions that covers before it starts. `il2ks reprocess --since` and `--until` take a time too, as
  in `--since '2026-09-01 18:00'`.
- **Filter the sortie lists by incident:** taxi/takeoff accidents, botched landings or being strafed on the ground, on the site-wide list
  and a player's list.
- **The incidents as extra columns:** taxi/takeoff accidents, botched landings and strafed on the ground on the mission, aircraft and
  player lists.
- **Three new ground medals:** Firestorm, for ground targets destroyed within two minutes in one sortie, Flak Dodger, for ground kills in one
  life of attack sorties, and Surgical Strike, for the highest ground score per hour on target.
- **Mud Mover and Tour Mud Mover:** the ground side of Sky Hunter, a ribbon for the ground targets destroyed in a tour, and all time at five
  times the tiers.
- **An all-time Jet Hunter:** it counts all your tours against tiers of 5, 15, 25 and 50. The one-tour medal is now Tour Jet Hunter.
- **Several web workers now work on Windows too:** set `[web] workers` to use more processor cores (about 60 pages a
  second with 2 workers, about 100 with 4, in our tests). The default stays at 1. The README has a new "Common issues"
  section for a slow site.

### Changed

- **"Taxi accidents" are now "Taxi/takeoff accidents" and include crashes just after takeoff:** an aircraft that leaves the ground
  and crashes within a minute, with nobody else involved and the pilot still at the controls, counts too (into the trees or a hill). In
  our sample of 210 missions that adds 127 to the 768 before. The window is `takeoff_accident_s` in the `[replay]` section of
  `il2ks.toml`.
- **The web server is now hypercorn instead of granian**, because granian runs one worker on Windows. Upgrading needs no
  change: the settings, the port and the logs stay the same. If a worker dies, the whole web server stops and `il2ks run`
  starts it again. If you run plain `il2ks web` under your own service manager, make sure it also restarts on exit code 0.
- **Long runs update one line:** from 200 missions, `il2ks ingest` and `il2ks reprocess` on a terminal show `Mission 50/210 ETA: 1m 22s` in
  place, with the last warnings and errors under it. The estimate stops at the last mission: the rebuild after it logs as usual. The log file
  keeps every line, and a pipe, a service or a shorter run prints as before.
- **The sortie page has every figure of the extra columns:** air and ground score, kills by players and AI, assists, friendly fire and
  accuracy sit in a plain block under the summary.
- **Friendly-fire kills are split into air and ground** everywhere: the profile, the lists, and the aircraft, mission and sortie pages.
  The score penalty is unchanged. A saved link that shows the old "Friendly kills" column simply leaves it out.
- **The hall of shame has four tiles across the full width,** with the quip under them.
- **A profile lists achievements best first:** the highest tier, within it the rarest, then the newest, instead of in a fixed order.
- **The early bailout penalty only applies to a sound aircraft:** it is there to stop pilots bailing out to deny a kill, so a pilot who damaged the
  aircraft themselves first (20% or more) no longer pays it.
- **A sortie page says why it lost points:** a notice names the early bailout or friendly-fire penalty, with the points and the score they came
  off.
- **The Jet and Prop tags on the aircraft list** sit level with the name, with more room around them.
- **Limping Home has a platinum tier:** the tiers are 1, 3, 5 and 10, so five landings earn gold and ten earn platinum.
- **Ribbons have their own section below all the medals** on the achievements pages, drawn as ribbons instead of medals.
- **The summaries on aircraft and mission pages give incident shares with two decimals,** like 1.23%.

### Fixed

- **The header no longer changes between pages on a first visit:** the page often painted before the heading font arrived, so the wider
  fallback font wrapped the menu onto a second row until the next page. The heading font now loads first.
- **No more lone items:** the menu, the profile links and the board and medal tabs no longer leave one link alone on a last row, and text
  no longer ends on a line of one word.
- **A backup on Windows no longer fails** when the copy to the second folder is still reading an old backup that rotation wants to delete.
- **Tooltips of the columns:** a column without a description showed its internal name, as in `air_points`. Every column that needs one now has
  a real description, also the default columns of the sortie lists, leaderboards, mission and aircraft tables.
- **Column descriptions match the rules:** time on target ends a minute after the last release, a jet shooting down a propeller aircraft is no
  encounter and no duel between types, and the Assists column of the air board counts assists on aircraft only.
- **Attack proficiency shows one decimal on the aircraft pages too,** as on its board.
- **The optional Attack sorties column of the aircraft list is called Share of attack sorties,** as it shows a percentage.
- **The optional Tour column of the mission list is left-aligned,** like other text.
- **The note under the profile's ratios no longer touches their box.**
- **Scrolling a table sideways no longer draws links over the pinned column:** on the sortie lists a mission name showed through the date.
- **`il2ks reprocess` reports its progress:** a line per mission says how many are done and how many are left, as `il2ks ingest` reports each
  mission.
- **The percent sign follows the language:** German, Spanish, French and Russian write a no-break space before it (1,23 %).

### Templates changed (check your custom/ overrides)

- `site.css`: v6.1 -> v8.1 and `players.css`: v1.0 -> v4.0 (breaking). The `.kv--compact` styles moved from `players.css` to `site.css`, and
  `site.css` gained `.stat-columns`, `.stat-columns__title` and `.ribbon--bare`. A copied `site.css` needs these styles, or the new sections
  show unstyled.
- `achievements/overview.html`: v1.1 -> v2.0 and `players/achievements.html`: v1.0 -> v2.0 (breaking). Their rows moved to the new
  `achievements/_overview_rows.html` and `players/_achievement_rows.html`, and the views pass `medal_rows` and `ribbon_rows` instead of `rows`.
- `aircraft/list.html`: v1.0 -> v3.0 (breaking): the name cell is wrapped in `.aircraft-table__identity`, so the tag sits level with the name, and
  attack proficiency uses the new `attack_rate` filter (one decimal).
- `sorties/detail.html`: v1.0 -> v1.1 and `sorties/parts/figures.html` (new): the plain figures. An override of the page keeps working, without them.
- `sorties/all.html` and `sorties/list.html`: v1.0 -> v1.2 (the incident filter, column descriptions). `admin/il2ks_ingest_status.html`: v3.1 -> v3.2 (the time span
  button), with the new `admin/il2ks_reprocess_span.html` and `admin/il2ks_span_label.html`.
- Friendly-fire kills split, or percent signs by language: `players/detail_aircraft_build.html` and `players/detail_ground.html`: v1.0 -> v1.1, `sorties/parts/kills.html` and
  `sorties/parts/list_row.html`: v1.0 -> v1.1, `sorties/parts/summary.html`: v1.1 -> v1.2. An override keeps working, with the old wording.
- `aircraft/detail.html`: v1.0 -> v1.2 and `missions/detail.html`: v1.2 -> v1.4: they include the new `aircraft/detail_secondary.html` and
  `missions/detail_secondary.html` (both new), and more headers have a description.
- More headers with a description: `home.html`: v3.0 -> v3.1, `leaderboards/home_block.html`, `leaderboards/table.html`, `missions/_rows.html`,
  `missions/list.html`, `players/detail_aircraft.html`, `players/detail_pve.html` and `players/detail_recent.html`: v1.0 -> v1.1. An override
  keeps working, without them. `sorties/parts/notices.html`: v1.0 -> v1.1 (the penalty notices).
- `players/detail_shame.html`: v2.0 -> v2.3 (four tiles, the quip under them), `players/detail_totals.html`: v1.2 -> v1.4 (assists, gun accuracy,
  friendly-fire kills split),
  `sorties/parts/header.html`: v1.1 -> v1.2 (the new badge).
- `il2ks/base.html`: v2.1 -> v2.2 (the font preload, one space between menu items) and `leaderboards.css`: v1.0 -> v1.1. An override keeps
  working, without the preload or the balanced rows.

## 0.5.4 (2026-11-01)

### Fixed

- **Medals in "Recently earned" on the home page have their tier colours again:** bronze, silver, gold and platinum badges, and the
  rings around rare tiers, showed in grey there. Nothing to run after the upgrade.

### Templates changed (check your custom/ overrides)

- `site.css`: v6.0 -> v6.1. An override keeps working, but stays grey until you delete the `--medal` line under `.feed__item`.

## 0.5.3 (2026-11-01)

### Changed

- **Fences of your own side are no longer friendly fire:** destroying one gives no friendly kill and no score penalty, and hits
  and damage on it are not counted as friendly either. The timeline shows it as "Fence destroyed".
- Run `il2ks reprocess --all` once after the upgrade to apply this to the missions you already have (it rebuilds the aggregates
  too, and needs your archived logs). Coming from 0.5.1 or older, this one run covers 0.5.2 as well.

## 0.5.2 (2026-11-01)

### Added

- **Jet Hunter:** a new medal for shooting down jets from a propeller aircraft (1 / 3 / 5 / 10).

### Changed

- **Bomber Hunter** now also counts AI bombers, attackers and transports, not only those flown by other pilots.
- **Type Collector** now tops out at 8 types: the game has no more than that, so the 12-type tier could not be reached.
- **Fences are no longer ground kills:** destroying one gives no kill, assist or points. The timeline still shows it, as "Fence
  destroyed". Friendly-fire kills are counted as before.
- **Run `il2ks reprocess --all` once after the upgrade** to apply all this to the missions you already have (it rebuilds the
  aggregates too, and needs your archived logs). Until then old missions keep their fence kills, and the medal changes apply
  from the next mission a pilot flies. Ground kills, scores and leaderboards go down by the fences a pilot had.

### Removed

- **Negative medals:** Ramp Rash, Wrong Team, Sitting Duck and Hard Landing are gone, with their tour variants, so nobody has a
  reason to hunt them. **Contact Sport** (ramming) goes for the same reason. The upgrade removes the ones already earned. The hall
  of shame on the profile stays, with its numbers and quips.

### Templates changed (check your custom/ overrides)

- `sorties/parts/timeline_table.html` has a new row for destroyed fences. An override keeps working, but calls a group of fences
  "ground targets".
- `players/detail_shame.html`: v1.0 -> v2.0 (the ribbons under the tiles are gone; `medal_set.shame` no longer exists, so an
  override that still reads it just shows nothing there).

## 0.5.1 (2026-10-31)

### Fixed

- **Sorties of a running mission:** a pilot who is still flying no longer gets a "Sortie ended" row in the timeline or an end
  time on the sortie page. Statuses such as Survived and Healthy stay as they were.

### Changed

- **Online now:** the player table on the front page is collapsed at first; open it under **Players**. The player count and
  the sides stay visible, and an opened list stays open while the box refreshes.

### Templates changed (check your custom/ overrides)

- `static/il2ks/il2ks.js`: v3.0 -> v4.0 (keeps the player table open across refreshes).
- `components/online_now_body.html` and `sorties/parts/summary.html` have cosmetic version bumps. An override of them keeps
  working, but shows the player table open and the end time of a sortie still going.

## 0.5.0 (2026-10-31)

### Added

- **Stats delay:** the stats of a running mission now lag the game, so nobody can use the site to gain an advantage in a
  fight. Sides, aircraft, flight times, kills, sorties and the totals they feed are held back; how many players are online
  and their names stay up to date. Set the time with `delay_minutes` under `[live]` in `il2ks.toml`. **The default is 10
  minutes**; `0` switches it off and gives the old behaviour.
- The online now box and the pages of a running mission tell visitors there is a delay, but not how long.

### Changed

- **A mission that ends is published at once:** its complete stats appear within one look at the log (30 s by default)
  instead of waiting for the final save, which follows as before.

- **Sortie timeline:** a player's name now links to that player's sortie, where the event happened. The other tables on
  the page still link to the profile.

### Fixed

- **Mission times hours off, sorties "in the future":** DServer names its logs in its own clock, and il2ks read those names
  as UTC when it didn't know the zone. Windows servers without `[server] timezone` now get the zone from Windows. When a log
  file's real time disagrees with its name by a time zone's worth, ingest and `il2ks doctor` say so, and doctor also
  flags missions that start in the future. A container can't see its host's zone, so with Docker check
  `IL2KS_SERVER_TIMEZONE` in `.env`.

### Upgrading

- One new database migration; it runs by itself as usual.
- **Mission times wrong?** Run `il2ks doctor`, set `[server] timezone` to the game server's zone if it says so, then
  `il2ks reprocess --all` and `il2ks rebuild-aggregates --retour` to correct the missions you already have.
- A Windows server without `[server] timezone` now reads new missions in its real zone. Run the same two commands to
  bring older missions in line.

### Templates changed (check your custom/ overrides)

- `components/online_now_body.html`, `missions/detail.html` and `sorties/parts/header.html` show the delay notice. These are
  cosmetic version bumps, so nothing breaks, but an override of them won't show the notice until you add it.
- `sorties/parts/timeline_table.html` and `sorties/parts/who.html`: the timeline's new link.

## 0.4.0 (2026-10-31)

### Added

- **Delete tours:** **Admin > Tours** has a **Delete…** link on every tour (administrators only). With manual tours that
  have a neighbour it asks what happens to the missions: delete them too, or keep them in the tour next to it. Monthly
  and N-day tours are their time period, so their missions always go with them.
- **Delete missions:** select missions under **Admin > Missions** and choose **Delete selected missions…**. A tour that
  loses its last mission goes as well, unless tours are started by hand.
- Deleted missions stay deleted: the archived logs stay on disk, but no later ingest or reprocess brings them back, also
  not **Delete all data and reprocess**. Pilots who flew nowhere else go with them, and all totals, ratings, medals and
  leaderboards are worked out again. A backup is made first. **Admin > Removed missions** lists them: delete a row and
  reprocess all missions to bring a mission back, or run `il2ks reprocess --mission <uid>`.

### Upgrading

- One new database migration; it runs by itself as usual.

## 0.3.2 (2026-10-31)

### Added

- **Crash quips:** a sortie that ends in a crash that was only the pilot's doing, with no damage or hit from anybody else,
  gets its own line on the sortie page. Six lines, in every language. You can switch the new spot off or edit it under
  **Admin > Site texts > Quips**.

## 0.3.1 (2026-10-30)

### Fixed

- **Sortie timeline:** the exact feet, the map position and the ammunition's designation now appear as soon as you hover
  the value (dotted underline), and on a phone when you tap it. They were browser tooltips, which take a while to show
  and don't exist on a touch screen.
- **Mission page:** the sortie tables no longer cut off "Ground" and the help marker of "Assists", and the Russian role
  badge no longer runs over the outcome. Long column names and badges, as in the translations, wrap instead of running
  into the next column.

### Templates changed (check your custom/ overrides)

- `static/il2ks/il2ks.js`: v2.0 -> v3.0, `static/il2ks/site.css`: v5.0 -> v6.0 (`.cell-hint`; column description tooltips
  no longer need to sit in a `th`).
- `static/il2ks/missions.css`: v1.0 -> v2.2 (the sortie table's column widths and padding; wrapping badges).

## 0.3.0 (2026-10-28)

### Changed

- **Navigation:** Leaderboards moved from the top bar into the **History** menu (Missions, Sorties, Leaderboards), which
  leaves room for your own links in the header (with a few of them it used to wrap to a second row). The home
  page still shows its leaderboard block with "All leaderboards". On a phone the History menu opens leftwards so it
  stays on screen.

## 0.2.1 (2026-10-27)

### Fixed

- **Online now** no longer lists a "Joining…" player that never goes away. It was the dedicated server's own login, which
  the log records as a player connecting at the start of every mission; it was also counted in "players online".
- **Home page:** while a mission is running (and "Show sorties of the running mission" is on), the block under Online now
  is titled **Current mission** and shows the Live badge instead of a winner. It used to say "Last mission", which read as
  the previous mission. Once the mission ends, or with the setting off, it is the last finished mission as before.
- **Home page:** the "Sorties per side" share moved from the last-mission block to under the Server activity chart. It is
  the share for the selected tour (or all time), not for that mission, and read as the mission's split there.

### Docs

- The README has an **Upgrade** section: the steps for the Windows installer, uv and Docker in one place.

### Templates changed (check your custom/ overrides)

- `il2ks/home.html`: v2.0 -> v3.0 (the share line moved to `il2ks/home_activity.html`).

## 0.2.0 (2026-10-27)

### New

- **Notice banner.** A one-line notice under the header on every public page (event tonight, maintenance, rules change),
  with the levels "info" and "warning". Set it in the admin under the site settings.
- **Markdown pages.** A navigation link can now open a page you write yourself in the admin (server intro, rules, flavour
  text), with a preview. A page has one base text and optional translations for the site's other languages. Pictures can be
  linked from other sites (`![](https://...)`). The text is sanitized when you save it.
- **Side balance.** The mission page shows how many pilots each side had (averaged over the mission time). The home page and
  the leaderboards show the share of sorties flown per side. A pilot's profile shows how often they flew each side and how
  often they flew for the side with fewer pilots (marked like the other ratios).
- **Altitude column** in the sortie timeline (feet/Angels for BLUFOR, metres for REDFOR).
- **Delete all data and reprocess.** A second button next to "Reprocess all" in the admin (superusers) and
  `il2ks reprocess --all --wipe` on the command line. It deletes every ingested row and rebuilds everything from the mission
  archive, so the tours are cut again from the current tour settings. Admin settings, hidden players and missions, name
  overrides and manual tour names are kept. It asks you to type a confirmation and takes a backup first.
- **Admin login lockout.** After 5 failed logins for an account from one address, that pair is locked for 15 minutes
  (a looser limit applies per address). `il2ks doctor` lists locked accounts; `il2ks admin unlock` releases them.
- **Backup copy.** `[backup] copy_to` copies every backup to a second folder (another drive or a share);
  `copy_archive = true` mirrors the mission archive there too.
- **More `il2ks doctor` checks:** pages served without compression through your public address (behind your own reverse
  proxy), and the backup copy folder.
- **`/healthz`** for uptime monitors (200 when the database answers, never cached), **`sitemap.xml`** and **`robots.txt`**
  (visible players, aircraft, missions and Markdown pages, so pilots can find the server by searching their nickname).
- The footer's "Powered by il2ks" now links to the GitHub project.

### Changed

- **Template versions are now `vN.M`.** `N` changes only when your copy of a file could break or miss new content (a block,
  an include, a variable, a URL name or an element the stylesheet or script targets was added or removed). `M` is
  everything else (wording, colours, spacing). An override on an older N is **outdated** (red banner in the admin,
  installer message, `il2ks doctor` warning). An override on an older M is only **behind**: it is shown by
  `il2ks custom list`, with no banner. 0.1.0 files count as `N.0`.
- The built-in heading font now uses `font-display: optional`: if it is not loaded in time, the fallback font stays for
  that page view, so the layout no longer jumps.
- The login throttle was replaced by the lockout above.

### Security

- **Pillow 13.0.0** (image handling): fixes several published vulnerabilities in 12.x. The Windows installer and the
  Docker image pin it; a PyPI install now requires it.
- **Windows installer:** bundled Python 3.13.18 (was 3.13.16). HTTPS trust on Windows comes from the Windows certificate
  store, which Windows Update keeps current (for example after the October 2026 eMudhra CA compromise).
- **Docker:** Caddy 2.11.7 (fixes a crash in 2.11.6 when proxying over HTTP/2). The image is now based on Debian 13
  (trixie, was 12) and takes Debian's newest CA certificates when it is built. Upgrade with `build --pull` (see the upgrade
  notes) so a rebuild also picks up Debian's security updates.
- All other dependencies updated to their latest versions.

### Project

- Dependabot (weekly, uv lock file, GitHub Actions and the Docker images, with a 7-day cooldown) and a blocking
  `pip-audit` check on every push and weekly.
- CI hardened: every GitHub Action pinned to a commit SHA, read-only tokens by default, no persisted git credentials,
  and a blocking zizmor check of the workflows.
- A guarded helper for outbound web requests (private and internal addresses are refused). Nothing uses it yet; the
  optional `[outbound] allow_private` list is for later features that fetch from a LAN.

### Upgrade notes

1. Update il2ks as usual. Database migrations run automatically. **Docker:** rebuild with
   `docker compose -f docker/compose.yaml build --pull`, then `up -d` (the base image moved to Debian 13).
2. Run `il2ks reprocess --all` once. Old sorties have no side balance data, so the underdog flags and the mission
   averages stay empty until you do. (Skip it if you use "Delete all data and reprocess".)
3. New settings in `il2ks.toml`, all optional with defaults: `[web] login_attempts` (5) and `login_lockout_minutes` (15);
   `[backup] copy_to` and `copy_archive`; `[outbound] allow_private`.
4. **If you customized templates, styles or scripts in `custom/`**, check your copies of these files (they have a new N;
   run `il2ks custom list` to see which of yours are outdated):
   - `static/il2ks/il2ks.js`
   - `static/il2ks/site.css`
   - `static/il2ks/sorties.css`
   - `static/il2ks_admin/ingest_status.css`
   - `static/il2ks_admin/theme_editor.css`
   - `templates/admin/il2ks_ingest_status.html`
   - `templates/il2ks/base.html`
   - `templates/il2ks/home.html`
   - `templates/il2ks/leaderboards/list.html`
   - `templates/il2ks/sorties/parts/timeline_table.html`

   Files not listed here only changed cosmetically; a copy of one stays valid.

## 0.1.0 (2026-10-05)

Initial version (public beta).
