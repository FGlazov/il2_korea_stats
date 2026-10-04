# Making the site look like yours

There are two levels. Most server owners only need the first.

1. **Branding in the admin**: no files, nothing to restart. Title, logo, color, texts, links.
2. **`custom/` overrides**: replace any page template, stylesheet or image with your own file. For when branding is not
   enough. Survives upgrades; il2ks tells you when an upgrade changed something you replaced.

## 1. Branding in the admin

Open `https://your-site/admin/` and sign in with the admin account (create one with `il2ks createadmin`). Under **Site
settings** you can change:

- **Site title** and **server name** (the name of your game server),
- **Description**: a short text shown on the home page,
- **Logo**: upload a PNG, JPEG or WebP picture. (SVG is not accepted for uploads, because an SVG file can carry
  scripts. See the next section if you need SVG.)
- **Fonts**: one for headings and one for the text (see below),
- **Colors**: every color of the site, separately for the light and the dark theme (see below),
- **Navigation links**: your own links (Discord, forum, Patreon, ...) in the top menu (see below),
- **Coalition names**: what "REDFOR" and "BLUFOR" are called on your pages.

Changes show on the site at once.

### Navigation links

Add as many links as you like under **Navigation links, in order**: a label, an address, an optional icon (Discord,
forum, Patreon or a generic link chain) and a number that sets the order (lower numbers first; il2ks renumbers them 1, 2,
3 ... when you save). They appear in the top menu **after** the built-in ones (Missions, Players, Aircraft,
Leaderboards) and, as a plain list, in the footer. Only full `http://` and `https://` addresses are accepted
(`javascript:`, `mailto:`, relative paths and the like are refused). The links open in a new tab, with `rel="noopener
noreferrer"`, so the other site cannot reach back into yours or learn where the visitor came from; screen readers are
told that the link opens a new tab. Tick **Delete** on a row to remove a link. (Links you had under the old "Links" setting
were moved here by the upgrade, in the same order.)

**How many fit?** The menu never overflows: when it is full, the extra links wrap onto a second row (the header gets
taller, nothing is cut off, there is never a horizontal scroll bar). We measured the header with Chromium at 360, 768, 1280
and 1920 px wide (the page content is at most 1240 px wide, so 1920 looks like 1280):

| Labels | 1280 and 1920 px | 768 px | 360 px (phone) |
|---|---|---|---|
| Short (`Discord`, `Forum`, `Patreon`, `Wiki`, ...) | 3 links stay on the same row; the 4th wraps | up to 5 | 2 (the built-in four already take two rows) |
| Long (`Join our Discord server`, `Support us on Patreon`) | 1 link; the 2nd wraps | 1 | 0 |

So we recommend **at most 3 links with short labels (one or two words)**; longer labels fit fewer. The admin form says
the same. More links still work, they just make the header two or three rows tall. (The test that measures this is
`tests/e2e/test_nav_layout.py`.)

### Colors

Under **Colors** you can change every color the site uses, in two columns: **Light mode** and **Dark mode** (visitors
switch with the sun/moon button in the header; the site follows their system setting until they do). The groups are
backgrounds, text, borders, the header band and the home banner, the accent (buttons, links), the two coalitions, the
status colors of badges and notices, and the charts. Each box takes `#RRGGBB`; an **empty box means the default color**.
The **×** button next to a box (and the colour picker, with JavaScript on) resets it. "Automatic" boxes (the link
color and the text on the accent) follow the accent unless you fill them in.

**Start from a color scheme** replaces all colors with one of a few ready-made schemes (Steel blue, Desert sand, High
contrast) or with "Default" (clears everything) when you save. You can adjust single colors afterwards.

il2ks checks the readability of the colors you saved (WCAG contrast ratios: body text, links, buttons, badges, the
header). Poor combinations produce a yellow warning after saving; **nothing is blocked**, so a deliberate choice stays
possible. The one accent color of older versions became the accent in both modes.

Only exact `#RRGGBB` values are ever written into the page: nothing else can get through, even from a hand-edited
database. The colors are one small `<style>` block that sets the `--il2-*` variables (see "Colours" below).

### Fonts

**Heading font** and **Body font** pick from a fixed list: the condensed headline font that ships with il2ks, and
system fonts (system sans-serif, humanist sans-serif, serif, slab serif, monospace). They use fonts the visitor's device
already has (each choice lists fallbacks, so it always shows something readable), and **nothing is loaded from another
website**, so your visitors' privacy is not touched. Uploading your own font file is not supported; if you want one,
put the `.woff2` in `custom/static/` and add an `@font-face` and a `--il2-font-display` / `--pico-font-family` line in a
small stylesheet from the `head` block (section 2).

## 2. `custom/` overrides

Inside your **data folder** (the folder with the database and `logs/`; `il2ks.toml` says where it is) il2ks keeps a
`custom` folder with two sub-folders:

```
<data folder>/
  custom/
    templates/     your versions of page templates
    static/        your versions of stylesheets, images, scripts, or new files
```

The rule is simple: **a file in `custom/` with the same path as a built-in file replaces it.** Built-in files are never
touched, so an upgrade cannot overwrite your work, and deleting your file brings the original back.

`custom/` is part of `il2ks backup`.

### Copy a built-in file to start from

Don't write a template from nothing. Copy the built-in one and edit the copy:

```
il2ks custom list --builtin             # every built-in file you can override
il2ks custom copy il2ks/base.html       # a template
il2ks custom copy static/admin/css/base.css   # a static file (write "static/" first when the name could be either)
```

Each command prints where the copy went, for example `<data folder>/custom/templates/il2ks/base.html`. Open that file in
any text editor. **Keep the first line of the file**: it is the version line (see below). Also recorded: a note of what
the original looked like at that moment (kept in `custom/.il2ks-overrides.json`: do not delete it).

**Restart il2ks afterwards** (stop `il2ks run` with Ctrl+C and start it again, or restart the service; see
[install.md](install.md#upgrading)). Template and static changes are picked up at start. Static files are collected and
given a fingerprinted file name at start, so visitors' browsers fetch the new version straight away.

Other things you can do:

- **Add a new file**, not replacing anything: put it in `custom/static/`, for example `custom/static/my-banner.png`,
  and refer to it from one of your templates with `{% static 'my-banner.png' %}`.
- **An SVG logo**: put it in `custom/static/` and use it from your template. Only people with access to the machine
  can put files there, which is why this is allowed while uploads are not.
- **Admin look**: the admin's own templates and files (`admin/base.html`, `admin/css/base.css`, ...) can be overridden
  in the same way. (Leave `admin/base_site.html` alone: it is il2ks's own addition that shows the red warning banner
  described below. If you replace it, the banner is gone; `il2ks doctor` and the log still report the problems.)

### When il2ks is upgraded

An upgrade may change a page you replaced. Your copy then keeps the old behaviour: the page may break, or silently miss
new content. To make this visible, **every built-in template, stylesheet and script has a version number** in its first
line, and the number goes up whenever the file changes:

```
{# il2ks-template: templates/il2ks/base.html v3 - copy this line along when you override #}
```

(Stylesheets and scripts use `/* ... */` instead of `{# ... #}`. Images are replaced as a whole, so they have no
version.) The line is copied along with the file, so your override says which version it is based on. When il2ks
starts, it compares that number with the number in the file it ships now.

**The red banner.** If any override is based on an older version, on an unknown one, or on a file that no longer
exists, every page of the admin (`/admin/`, only visible to people who can log in there) shows a red box that lists
the files and what to do. Visitors never see it. The same list is written to the log and printed when `il2ks run`
starts, `il2ks doctor` lists each file as a warning, and `il2ks custom list` shows a state for every file:

| State | Meaning |
|---|---|
| `up to date` | Based on the version il2ks ships now. |
| `OUT OF DATE` | Based on an older version (both numbers are shown). The built-in page changed: yours may break or hide new things. |
| `NO VERSION` | The version line is missing (a hand-made copy, or you deleted it), so il2ks can't tell. Treated like out of date. |
| `NEWER` | Based on a newer version than this il2ks has (il2ks was downgraded). |
| `ORPHAN` | il2ks no longer has a built-in file of that name. The override probably does nothing now: delete it, or keep it if it is deliberate. |
| `yours only` | A new file of your own that replaces nothing. Nothing to worry about. |
| `unchecked` | Replaces a built-in file that has no version (a vendored library, Django's own admin files), so il2ks can't tell. |

**Bringing an override up to date**:

1. See what differs: `il2ks custom diff templates/il2ks/base.html`. It compares your file with the built-in one as it is
   now (il2ks does not keep old versions, so the differences are the upgrade's changes plus your own edits). Lines
   starting with `-` are only in your file, lines starting with `+` only in the built-in one. For a visual comparison
   use any "compare files" tool (VS Code, WinMerge) on the two paths `il2ks custom list` shows.
2. Bring over what you want into your file.
3. Tell il2ks you are done: `il2ks custom accept templates/il2ks/base.html`. It sets the version line of your file to the
   current number (and nothing else in it). You can also edit the number by hand.
4. Restart il2ks. The warning is gone until the next time the built-in file changes.

If you don't have any edits you want to keep: `il2ks custom copy --force <path>` throws your file away and copies the
new built-in one.

Template names, their `{% block %}` areas and the variables they receive are the "API" of customizing. They change
rarely and the release notes list every file whose version went up, but this is the reason to override as little as possible: one small
template that fills a block is easier to keep up to date than a copy of a whole page.

### What you can override

**The page frame** — `il2ks/base.html`. Every page extends it. Blocks:

| Block | What it holds |
|---|---|
| `title` | The browser tab title (default: the page title, then the site title) |
| `head` | Extra tags in `<head>`: meta tags, an extra stylesheet |
| `nav` | The header bar: logo, site title, menu, search box |
| `content` | The page itself |
| `footer` | Server name, your links, "Powered by il2ks", when the data was last updated |
| `scripts` | Extra scripts at the end of the page |

Variables available on every page: `site` (your site settings: `site.site_title`, `site.server_name`, `site.description`,
`site.redfor_name`, `site.blufor_name`, ...), `logo_url`, `nav_links` (your navigation links: `label`, `url`, `icon`; the
default `nav` block lists them after the built-in ones), `site_links` (the same as label/URL pairs, used by the footer),
`theme_css` (the color and font overrides of the admin as one `:root{...}` rule, already safe: print it inside
`<style>`), `data_updated` (when the stats last changed), `il2ks_version`, and `page_title`.

**Small building blocks** — `il2ks/components/*.html`: tables, badges, stat tiles, filters, pagination, notices. Each file
starts with a comment that lists the variables it receives. Overriding one of these changes it on every page that uses it.
"Online now" is two of them: `online_now.html` (the section that refreshes itself every few seconds) and
`online_now_body.html` (the player table inside it, also what the `/live/` address returns).

**Pages** — `il2ks/home.html`, `il2ks/missions/`, `il2ks/players/`, `il2ks/sorties/`, and the error pages `404.html` and
`500.html` (the 500 page can't use your site settings: it must work even when the database doesn't).

**Icons and images** — `il2ks/img/` under static. Every icon has a fixed name (for example `il2ks/img/outcome/landed.svg`),
so you can replace a single icon by putting your own SVG at `custom/static/il2ks/img/outcome/landed.svg`.

**Colours** — the stylesheet `il2ks/site.css` defines every colour once as a CSS variable in its first section
(`--il2-accent`, `--il2-bg`, ...; light and dark side by side with `light-dark()`), and nothing else in the stylesheets
contains a colour. The colors from the admin (section 1) are written after the stylesheet and override those variables.
For more than the admin offers, add your own small stylesheet through the `head` block instead of copying `site.css`: a
few `:root { --il2-...: ... }` lines are enough and survive upgrades. (The camouflage pattern of the header and the favicon
are images with their own colors: replace `il2ks/img/pattern/camo.svg` and `il2ks/img/brand/favicon.svg` to change them.)

### Problems

- *My change does not show:* restart il2ks. Check the path: `il2ks custom list` shows what it knows about; the file
  must sit at exactly the same relative path as the original (`custom/templates/il2ks/base.html`, not
  `custom/base.html`). A browser may still show the old stylesheet: press Ctrl+F5.
- *The page shows an error after I edited a template:* a typo in the template. Put the original back
  (`il2ks custom copy --force <path>`) and edit again in smaller steps. The web log in `logs/` names the line.
