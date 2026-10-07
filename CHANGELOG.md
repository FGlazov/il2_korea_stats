# Changelog

## Unreleased (0.2.0)

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

### Project

- Dependabot (weekly, uv lock file and GitHub Actions) and a blocking `pip-audit` check on every push and weekly.
- A guarded helper for outbound web requests (private and internal addresses are refused). Nothing uses it yet; the
  optional `[outbound] allow_private` list is for later features that fetch from a LAN.

### Upgrade notes

1. Update il2ks as usual. Database migrations run automatically.
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
