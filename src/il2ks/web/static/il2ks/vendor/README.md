# Vendored front-end files (NFR-OFF-1: nothing is loaded from a CDN at runtime)

| File | Project | Version | License |
|---|---|---|---|
| `pico.min.css` | Pico CSS (the default build, class-based, not the classless or conditional ones) | 2.1.1 | MIT, `LICENSE-pico.md` |
| `htmx.min.js` | htmx | 2.0.11 | 0BSD, `LICENSE-htmx.txt` |
| `BarlowCondensed-600.woff2`, `BarlowCondensed-700.woff2` | Barlow Condensed, latin subset, via `@fontsource/barlow-condensed` | 5.3.0 | SIL OFL 1.1, `LICENSE-barlow-condensed-OFL.txt` |

Fetched once, at development time, from the npm CDN:

```
https://cdn.jsdelivr.net/npm/@picocss/pico@2.1.1/css/pico.min.css
https://cdn.jsdelivr.net/npm/htmx.org@2.0.11/dist/htmx.min.js
https://cdn.jsdelivr.net/npm/@fontsource/barlow-condensed@5/files/barlow-condensed-latin-{600,700}-normal.woff2
```

To upgrade, replace the file, update the version here, and look at `/_styleguide/` in both themes.
The font is used for headings, the brand and big numbers only; table text uses the system font stack.
