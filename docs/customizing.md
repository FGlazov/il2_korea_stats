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
- **Accent color**: one color, as `#RRGGBB`, used for links and buttons. Empty = the default look.
- **Links**: for example your Discord or forum, as a label and an address.
- **Coalition names**: what "REDFOR" and "BLUFOR" are called on your pages.

Changes show on the site at once.

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
any text editor. Also copied: a note of what the original looked like at that moment (a "fingerprint", kept in
`custom/.il2ks-overrides.json`: do not delete it).

**Restart il2ks afterwards** (stop `il2ks run` with Ctrl+C and start it again, or restart the service; see
[install.md](install.md#upgrading)). Template and static changes are picked up at start. Static files are collected and
given a fingerprinted file name at start, so visitors' browsers fetch the new version straight away.

Other things you can do:

- **Add a new file**, not replacing anything: put it in `custom/static/`, for example `custom/static/my-banner.png`,
  and refer to it from one of your templates with `{% static 'my-banner.png' %}`.
- **An SVG logo**: put it in `custom/static/` and use it from your template. Only people with access to the machine
  can put files there, which is why this is allowed while uploads are not.
- **Admin look**: the admin's own templates and files (`admin/base.html`, `admin/css/base.css`, ...) can be overridden
  in the same way.

### When il2ks is upgraded

An upgrade may change a template you replaced, and your copy then no longer matches. il2ks notices:

```
il2ks custom list
il2ks doctor
```

Both say which overrides are out of date (the built-in file changed since you copied it), which have no built-in
original any more, and which files you placed by hand (they cannot be checked). For an out-of-date override, `custom
list` prints both file paths. Compare them (any "compare files" tool: VS Code, WinMerge, `diff`), bring the changes you
want into your file, and then tell il2ks you are done:

```
il2ks custom accept templates/il2ks/base.html
```

The warning stops until the next time the built-in file changes. (`il2ks custom copy --force <path>` throws your edits
away and copies the new built-in file again.)

Template names, their `{% block %}` areas and the variables they receive are the "API" of customizing. They change
rarely and the release notes say when they do, but this is the reason to override as little as possible: one small
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
`site.redfor_name`, `site.blufor_name`, ...), `logo_url`, `site_links` (your links as label/URL pairs), `data_updated` (when
the stats last changed), `il2ks_version`, and `page_title`.

**Small building blocks** — `il2ks/components/*.html`: tables, badges, stat tiles, filters, pagination, notices. Each file
starts with a comment that lists the variables it receives. Overriding one of these changes it on every page that uses it.
"Online now" is two of them: `online_now.html` (the section that refreshes itself every few seconds) and
`online_now_body.html` (the player table inside it, also what the `/live/` address returns).

**Pages** — `il2ks/home.html`, `il2ks/missions/`, `il2ks/players/`, `il2ks/sorties/`, and the error pages `404.html` and
`500.html` (the 500 page can't use your site settings: it must work even when the database doesn't).

**Icons and images** — `il2ks/img/` under static. Every icon has a fixed name (for example `il2ks/img/outcome/landed.svg`),
so you can replace a single icon by putting your own SVG at `custom/static/il2ks/img/outcome/landed.svg`.

**Colours** — the stylesheet `il2ks/site.css` defines every colour once as a CSS variable (`--il2-accent`, `--il2-bg`, ...).
The accent colour from the admin overrides `--il2-accent`. For more, add your own small stylesheet through the `head` block
instead of copying `site.css`: a few `:root { --il2-...: ... }` lines are enough and survive upgrades.

### Problems

- *My change does not show:* restart il2ks. Check the path: `il2ks custom list` shows what it knows about; the file
  must sit at exactly the same relative path as the original (`custom/templates/il2ks/base.html`, not
  `custom/base.html`). A browser may still show the old stylesheet: press Ctrl+F5.
- *The page shows an error after I edited a template:* a typo in the template. Put the original back
  (`il2ks custom copy --force <path>`) and edit again in smaller steps. The web log in `logs/` names the line.
