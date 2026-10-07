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
- **Logo**: upload a PNG, JPEG or WebP picture (up to 2 MB). (SVG is not accepted for uploads, because an SVG file can carry
  scripts. See the next section if you need SVG.)
- **Browser tab icon**: the logo is used automatically (made square with transparent padding, never cropped); you can upload
  a separate icon instead (a square picture of at least 180 × 180 pixels works best). Without a logo the built-in aircraft icon stays.
- **Header background** and **Home banner background**: optional pictures behind the top banner (every page) and behind the title
  and player search on the home page (see below),
- **Fonts**: one for headings and one for the text (see below),
- **Colors**: every color of the site, separately for the light and the dark theme (see below),
- **Navigation links**: your own links (Discord, forum, Patreon, ...) in the top menu (see below),
- **Pages**: your own pages written in Markdown (server rules, an introduction), each with its own link in the menu (see below),
- **Coalition names and emblems**: what "REDFOR" and "BLUFOR" are called on your pages, and which emblem each side shows (neutral by default).

Changes show on the site at once. Not what you were looking for? Hiding players, renaming tours and game objects are in
[settings.md](settings.md#the-admin-area); the scoring rules are in [settings.md](settings.md).

### Navigation links

Add as many links as you like under **Navigation links, in order**: a label, an address, an optional icon (Discord,
forum, Patreon or a generic link chain) and a number that sets the order (lower numbers first; il2ks renumbers them 1, 2,
3 ... when you save). They appear in the top menu **after** the built-in ones (Players, Aircraft, Leaderboards and the
History menu with Missions and Sorties) and, as a plain list, in the footer. Only full `http://` and `https://` addresses are accepted
(`javascript:`, `mailto:`, relative paths and the like are refused; to link to a page of your own see Pages below). The links open in a new tab, with `rel="noopener
noreferrer"`, so the other site cannot reach back into yours or learn where the visitor came from; screen readers are
told that the link opens a new tab. Tick **Delete** on a row to remove a link.

**How many fit?** The menu never overflows: when it is full, the extra links wrap onto a second row (the header gets
taller, nothing is cut off, there is never a horizontal scroll bar). We measured the header with Chromium at 360, 768, 1280
and 1920 px wide (the page content is at most 1240 px wide, so 1920 looks like 1280):

| Labels | 1280 and 1920 px | 768 px | 360 px (phone) |
|---|---|---|---|
| Short (`Discord`, `Forum`, `Patreon`, `Wiki`, ...) | 3 links stay on the same row; the 4th wraps | up to 5 | 2 (the built-in four already take two rows) |
| Long (`Join our Discord server`, `Support us on Patreon`) | 1 link; the 2nd wraps | 1 | 0 |

So we recommend **at most 3 links with short labels (one or two words)**; longer labels fit fewer. The admin form says
the same. More links still work, they just make the header two or three rows tall.

### Pages

For text that belongs on your site (an introduction, the server rules, how to join), open **Pages** in the admin and add
a page: a title, a short name (the address becomes `/p/<short name>/`) and the text in
[Markdown](https://commonmark.org/help/) (headings with `#`, `**bold**`, lists, links, tables, `![description](address)` for
a picture). **Preview** under each text box shows what visitors will see, without saving. To put the page in the menu,
add a navigation link and choose the page in its **Page** column instead of typing an address: a page link opens in the
same tab (an address still opens in a new one). You can have as many pages as you like, each with its own link.

- The text is checked and converted once, when you save; the page is then served as it is. Scripts, `javascript:` links,
  inline styles and the like are removed for safety, and only `http://`, `https://` and relative links are kept.
- **Pictures** can be linked from other sites (`![](https://example.org/map.png)`); the visitor's browser loads them from
  there, so they must be on an `https://` address. There is no upload; a link that stops working shows no picture.
- **Languages**: the main text is shown to everybody (English unless you change **Language of the base text**). Under
  **Translations** you can add a text for any of the site's languages; a visitor whose language has one sees it, everybody
  else sees the main text. Nothing is forced: no translation is needed. A translation may leave its title empty to keep the main title.
- Deleting a page also removes the navigation links that pointed at it.

### Colors

Under **Colors** you can change every color the site uses, in two columns: **Light mode** and **Dark mode** (visitors
switch with the sun/moon button in the header; the site follows their system setting until they do). The groups are
backgrounds, text, borders, the header band and the home banner, the accent (buttons, links), the two coalitions, the
status colors of badges and notices, the charts, the shadows and the achievement medals (bronze to platinum). Each box takes `#RRGGBB`; an **empty box means the default color**.
The **×** button next to a box (and the colour picker, with JavaScript on) resets it. "Automatic" boxes (the link
color and the text on the accent) follow the accent unless you fill them in.

**Start from a color scheme** replaces all colors with one of a few ready-made schemes (Steel blue, Desert sand, High
contrast) or with "Default" (clears everything) when you save. You can adjust single colors afterwards.

il2ks checks the readability of the colors you saved (WCAG contrast ratios: body text, links, buttons, badges, the
header). Poor combinations produce a yellow warning after saving; **nothing is blocked**, so a deliberate choice stays
possible.

**Keep the contrast.** The shipped look meets WCAG 2.1 AA (4.5:1 for text, 3:1 for large text and borders of controls) in both
modes, and the site's automated accessibility test holds it there. A custom theme or `site.css` override that lowers contrast
makes the site harder to read for many visitors, so keep the warnings above in mind and check both modes. A quick way is the
browser's dev tools (Lighthouse, or "Inspect" on a text element shows its contrast ratio); the il2ks developers' test is
`IL2KS_TEST_E2E=1 uv run pytest tests/e2e/test_accessibility.py -m e2e`.

Only exact `#RRGGBB` values are written into the page. The colors are one small `<style>` block that sets the
`--il2-*` variables (see "Colours" below).

### Fonts

**Heading font** and **Body font** pick from a list: the condensed headline font that ships with il2ks, and
system fonts (system sans-serif, humanist sans-serif, serif, slab serif, monospace). They use fonts the visitor's device
already has (each choice lists fallbacks, so it always shows something readable), and **nothing is loaded from another
website**, so your visitors' privacy is not touched.

**Your own font.** Under "Fonts" you can **upload a font file** (`.woff2`, preferred, or `.woff`; up to 2 MB) and use it
for headings, body text or both: pick where to use it in the same step, or choose it later in the two lists, where it
appears as "name (uploaded)". Up to six fonts can be kept; tick "Remove uploaded fonts" to delete one (a removed font
that was in use falls back to the default). A preview line shows each uploaded font.

- Only real WOFF2/WOFF files are accepted: the extension, the file header and the declared size must all match. SVG,
  TTF, OTF, EOT and anything else is refused.
- The font is stored on your server (next to the logo, in `<data folder>/media/branding/`), served from your own site
  and cached by browsers for a year. **Nothing is loaded from another website**, so visitor privacy is unchanged.
- il2ks writes the `@font-face` rule itself (with `font-display: swap`: text shows at once in a fallback font and
  switches when the file arrives). The font family name and file name are generated from the file's hash; nothing you type
  ends up in the CSS. One file is used for every weight, so bold text is the font's own cut, or a synthesized bold if
  the file has only one weight; upload a font with the weights you need (a variable font covers all of them).
- **Size matters.** Every visitor downloads the file once. The shipped site loads about 45 KB of fonts; a subset
  `.woff2` (Latin plus the scripts you need) is usually 15-40 KB, a full font with every script can be several hundred.
  il2ks warns when a file is over 150 KB; aim for under 100 KB.
- Check that the font's **license allows web use** (embedding on a website); many desktop-only licenses do not.

If you prefer to manage fonts by hand, you can still put a `.woff2` in `custom/static/` and add an `@font-face` and a
`--il2-font-display` / `--pico-font-family` line in a small stylesheet from the `head` block (section 2).

### Background pictures for the top banner and the home banner

Under **Header background** and **Home banner background** you can upload a picture each (PNG, JPEG or WebP, up to 4 MB; SVG is
refused). Recommended sizes: **1920 × 400 pixels** for the header, **1600 × 500 pixels** for the home banner (larger pictures
are scaled down to 2560 pixels wide). Without an upload nothing changes: the built-in camouflage and aircraft decoration stays.

- The picture is **cropped to fill** the banner, which is wider and shorter on a desktop and narrower and taller on a phone. The
  **Focus of the picture** (center, left, right, top, bottom) says which part always stays visible, so put the important part there.
- **Darkening (0 to 80 %)** lays the band color over the picture so the light text stays readable, in the light and the dark
  theme. The default is 70 %; bright or busy pictures need 80 %, dark pictures work with less.
- The same picture is used in the light and the dark theme (the darkening keeps the contrast in both).
- **Remove it and use the built-in decoration** switches back. The picture is checked, re-encoded (metadata removed) and stored
  next to the logo; the file you uploaded is never served itself.

### A large image on the front page

Under **Front page image** you can show a large picture on the front page, right below the title and player search, for
example a **map of the current situation** that another program regenerates. It is **off by default** (the front page then
looks as before).

- **Image file on the server**: the full path of a PNG, JPEG or WebP file on the machine that runs il2ks (a relative
  path starts in the data folder). il2ks looks at the file's modification time and size every few seconds (this works on
  Windows, Linux and network shares) and shows a changed picture within about ten seconds; there is nothing to restart.
- **Caption** (optional) and **Alternative text** (required: a short description for people who cannot see the picture).
  The page also shows "Updated" with the file's time.
- The file itself is **never published**: il2ks checks that it really is an image (at most 40 MB and 64 megapixels),
  decodes it and stores a clean re-encoded WebP copy (at most 2560 px wide, plus a 960 px version for phones) in
  `<data folder>/media/branding/`. An admin cannot use the path to read other files on the server. Clicking the image
  opens the full-size copy.
- A wrong path (missing, a folder, not an image) is refused when you save, with the reason. If the file later breaks (for
  example while the other program is half way through writing it), the last good picture stays and the admin page and
  `il2ks doctor` show the problem.
- **Docker**: the file must be inside the container: mount it (for example `- /srv/maps/situation.png:/maps/situation.png:ro`
  under `volumes:`) and enter the path as the container sees it (`/maps/situation.png`).
- **Windows service**: the service runs as `NT SERVICE\il2ks`, which needs read access to the file (and to a network share
  it can use without your login). Grant it with `icacls "D:\maps" /grant "NT SERVICE\il2ks:(OI)(CI)R"`.

### Quips

The site shows a short, light-hearted line ("quip") at a few highlight spots: the hall of shame on a pilot's profile, a
notable sortie (taxi accident, many kills, a long flight), the top pilots of the last mission, an empty tour. They are
**on by default** and you change nothing until you edit them. Open **Admin > Site texts > Quips**:

- **Show quips on the site** is the master switch. Untick it and no quip appears anywhere.
- Every spot has a description of when it shows (for example "Sortie: taxi accident"), and a choice:
  *Built-in quips only* (the default), *Built-in and my own*, *My own only*, or *No quip here*.
- Each built-in line has a **Hide** box. A page picks one line of the remaining ones, always the same line for the same
  pilot, sortie or mission (so a page does not change when reloaded).
- **Your own lines**: type a line, choose a language (empty = every language) and save; a saved line can be edited,
  switched off with *On* or removed with *Delete*. A line for one language is used only on pages in that language, so a
  site with players in several languages usually adds the same joke once per language, or leaves the language empty.
  If a spot is set to *My own only* and has no line for the page's language, it shows no quip there.
- Your lines are plain text: no HTML or formatting (it is always shown as typed), at most 200 characters, up to 20 per
  spot. There are no placeholders for now: a quip cannot contain a pilot name or a number.
- A hidden built-in line is remembered by its English text. If an il2ks update rewords that line, your hide no longer
  matches anything; the page then lists it as "no longer exists" with a **Forget** box, and the new wording shows up
  again until you hide it.

Saving refreshes the cached pages at once.

### Achievements

The medals, ribbons and hall-of-shame entries pilots earn are all **on by default** with their built-in names and
thresholds. Open **Admin > Site texts > Achievements** (needs the permission to change the site settings). Each
achievement has:

- **Switched on**. An achievement you switch off disappears from profiles, sortie pages, the overview, the holder lists,
  the home feed and the rarity figures, at once.
- **Own name and description per language** (hidden under "Own name and description per language"). Leave a language
  blank to show the built-in text, translated into that language. Texts are plain text (no HTML), at most 60 characters
  for a name and 300 for a description.
- **Tier thresholds**: the number each tier needs (for example 5, 10, 20, 50 air kills in one life). Whole numbers, each
  larger than the one before, and as many tiers as the built-in achievement has. Blank = the built-in numbers.
- **Reset to default**: tick it and save; the achievement goes back to on, with the built-in words and thresholds.

Names, descriptions and the on/off switch apply immediately. **Thresholds, and switching an achievement back on, need a
recompute**: who holds which tier is worked out when sorties are ingested. After you save, the page says "A recompute is
pending"; the watch process (`il2ks watch`, or `il2ks run`) then recomputes every pilot's achievements by itself at its
next tick, and `il2ks rebuild-aggregates` does it too. Until it ran the pages show the old thresholds; afterwards the
holders, the dates earned and the rarity percentages change. A switched-off achievement keeps no rows after a recompute
(switching it on again brings them back with the next one). There is no separate global switch: switch the individual
achievements off.

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

Your own `.css` and `.js` files are minified when they are collected, like the built-in ones: the copy visitors get is
smaller, your file in `custom/` stays as you wrote it (and `il2ks web --dev` serves it unminified). Ordinary `/* ... */`
comments are removed from the served copy; a comment that must stay in it, such as a license or credit line, starts with
`/*!`, for example `/*! Theme by Example Squadron, CC BY 4.0 */`.

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
line, and the number goes up whenever the file changes (from the first public release on; before it every file is `v1`,
and il2ks then also compares a fingerprint of the original that `il2ks custom copy` recorded, so a changed file is still
noticed):

```
{# il2ks-template: templates/il2ks/base.html v1 - copy this line along when you override #}
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

To see only the files that need attention, run `il2ks custom list --problems`. For scripts, `--json` prints the same
as machine-readable text, and `--fail-on-problems` ends with exit code 4 when something needs attention (the Windows
installer uses this at the end of an upgrade).

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
`site.redfor_name`, `site.blufor_name`, ...), `logo_url`, `nav_links` (your navigation links: `label`, `url`, `icon`, `internal` for a link to one of your pages; the
default `nav` block lists them after the built-in ones), `site_links` (the same as label/URL pairs, used by the footer),
`theme_css` (the color and font overrides of the admin as `@font-face` rules for uploaded fonts plus one `:root{...}` rule, already safe: print it inside
`<style>`), `data_updated` (when the stats last changed), `il2ks_version`, and `page_title`.

**Small building blocks** — `il2ks/components/*.html`: tables, badges, stat tiles, filters, pagination, notices. Each file
starts with a comment that lists the variables it receives. Overriding one of these changes it on every page that uses it.
"Online now" is two of them: `online_now.html` (the section that refreshes itself every few seconds) and
`online_now_body.html` (the player table inside it, also what the `/live/` address returns).

**Pages** — `il2ks/home.html`, `il2ks/missions/`, `il2ks/players/`, `il2ks/sorties/`, `il2ks/aircraft/`,
`il2ks/leaderboards/`, `il2ks/achievements/`, `il2ks/streaks/`, and the error pages `404.html` and
`500.html` (the 500 page can't use your site settings: it must work even when the database doesn't).

**Icons and images** — `il2ks/img/` under static. Every icon has a fixed name (for example `il2ks/img/outcome/landed.svg`),
so you can replace a single icon by putting your own SVG at `custom/static/il2ks/img/outcome/landed.svg`.

*Your own SVG icons.* The site merges all icon files into one sprite (`/sprite.svg`), so a custom file must be a plain,
self-contained SVG:

- A single `<svg>` root, drawn on a grid with a `viewBox` (for example `viewBox="0 0 24 24"`). Without one, a numeric
  `width` and `height` (`24` or `24px`) are used; `%` or `em` sizes cannot be.
- Colour with `currentColor` (`fill="currentColor"` or `stroke="currentColor"` on the root or the shapes), so the icon
  follows the light or dark theme. No text inside the icon.
- No `<style>` blocks, and no ids or gradients that other icons could also use: all icons share one document, so a
  clashing id changes another icon.
- The editor's extras are fine and are removed automatically: the `<?xml?>` line, comments, a DOCTYPE, `metadata`,
  Inkscape / Sodipodi / Illustrator elements and attributes, and a UTF-8 BOM. `xlink:href="#id"` works; links to other
  files do not. Scripts, `foreignObject` and event attributes are removed.
- A file that still cannot be read as XML (a broken tag, a declared entity) is left out of the sprite and logged, and
  the site behaves as if the file did not exist (an aircraft falls back to the generic jet or propeller icon). `il2ks
  doctor` lists such files.
- `brand/favicon.svg` and `pattern/` are not icons: they are used as separate image files and are not in the sprite.

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
