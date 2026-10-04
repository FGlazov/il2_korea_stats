"""Colour themes and font choices for the public pages (TD-25, FR-ADM-2).

Every colour of the site is a CSS custom property in `static/il2ks/site.css` (section 1). `TOKENS` lists the ones a
server admin may change, with the shipped defaults (a test keeps this list and the stylesheet in step). The admin stores
only the overrides, per mode: `{"light": {"bg": "#E9E8E0"}, "dark": {...}}`. `theme_css` turns them into one
`:root { ... }` rule for a `<style>` block, and it can only ever emit what it builds itself:

- a token value is exactly `#RRGGBB` (re-validated here, so a hand-edited database row cannot inject CSS),
- a font is one of the fixed stacks below, chosen by key, or an uploaded font (`web.fonts`): its family and file name
  are generated from a content hash and re-validated here, and its `@font-face` uses `font-display: swap`,
- the final string is checked against a whitelist of characters before it is marked safe.

`contrast_warnings` compares the text/background pairs of the effective colours (WCAG 2.x ratios) and returns messages
for the admin; it never blocks a save.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from django.utils.functional import Promise
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import gettext_lazy as _

from il2ks.web.fonts import CustomFont, clean_fonts, font_face_css

type StrOrPromise = str | Promise
type Mode = Literal["light", "dark"]
type Theme = dict[Mode, dict[str, str]]
MODES: Final[tuple[Mode, Mode]] = ("light", "dark")

HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


@dataclass(frozen=True, slots=True)
class Token:
    """One themable colour. `light`/`dark` are the shipped defaults as '#rrggbb'; None = derived from other tokens
    (see `_derived`), which the admin sees as "automatic"."""

    key: str
    css_var: str
    label: StrOrPromise
    group: str
    light: str | None
    dark: str | None


GROUPS: Final[tuple[tuple[str, str], ...]] = (
    ("backgrounds", _("Backgrounds")),
    ("text", _("Text")),
    ("borders", _("Borders")),
    ("header", _("Header band and home banner")),
    ("accent", _("Accent, links and buttons")),
    ("coalitions", _("Coalitions")),
    ("status", _("Status colors (badges, notices)")),
    ("charts", _("Charts")),
    ("medals", _("Achievement medals")),
)


def _t(key: str, group: str, label: StrOrPromise, light: str | None, dark: str | None, var: str | None = None) -> Token:
    return Token(key, var or f"--il2-{key}", label, group, light, dark)


TOKENS: Final[tuple[Token, ...]] = (
    _t("bg", "backgrounds", _("Page background"), "#e9e8e0", "#11140f"),
    _t("surface", "backgrounds", _("Cards, tables, inputs"), "#f9f8f3", "#191d16"),
    _t("surface-2", "backgrounds", _("Table headers, quiet panels"), "#f0efe7", "#20251c"),
    _t("surface-hover", "backgrounds", _("Row and menu hover"), "#e8e6d8", "#272d21"),
    _t("text", "text", _("Body text"), "#24281f", "#dadfce"),
    _t("heading", "text", _("Headings"), "#161a11", "#f0f3e8"),
    _t("muted", "text", _("Secondary text"), "#636856", "#929a82"),
    _t("border", "borders", _("Borders"), "#d5d3c5", "#2d3427"),
    _t("border-strong", "borders", _("Strong borders"), "#bcb9a7", "#3f4837"),
    _t("shadow", "borders", _("Shadows"), "#1e2214", "#000000"),
    _t("band", "header", _("Header band"), "#2b3326", "#151a12"),
    _t("band-text", "header", _("Header text"), "#e9ecdd", "#e9ecdd"),
    _t("band-muted", "header", _("Header menu text"), "#aab294", "#aab294"),
    _t("band-strong", "header", _("Header text on hover"), "#ffffff", "#ffffff"),
    _t("accent", "accent", _("Accent (buttons, markers)"), "#a56814", "#a56814"),
    _t("accent-contrast", "accent", _("Text on the accent"), None, None),
    _t("link", "accent", _("Links"), None, None, var="--il2-primary"),
    _t("secondary", "accent", _("Secondary button text"), "#5d6650", "#a3ab92"),
    _t("secondary-bg", "accent", _("Secondary button"), "#5d6650", "#454e3b"),
    _t("secondary-contrast", "accent", _("Text on the secondary button"), "#ffffff", "#ffffff"),
    _t("redfor", "coalitions", _("REDFOR"), "#a12824", "#f47a73"),
    _t("blufor", "coalitions", _("BLUFOR"), "#1b56a0", "#70a9f4"),
    _t("green", "status", _("Good (green)"), "#136831", "#5dd08c"),
    _t("amber", "status", _("Caution (amber)"), "#7b5100", "#ecbb4a"),
    _t("orange", "status", _("Orange"), "#9f350a", "#f29458"),
    _t("red", "status", _("Bad (red)"), "#ab2121", "#f07c7c"),
    _t("blue", "status", _("Blue"), "#1b58a7", "#6eaaf2"),
    _t("teal", "status", _("Teal"), "#0d655f", "#54c9bd"),
    _t("purple", "status", _("Purple"), "#763dab", "#bf95ee"),
    _t("grey", "status", _("Neutral (grey)"), "#4e5c67", "#9aa8b4"),
    _t("chart-1", "charts", _("Chart series 1"), "#2a6fa8", "#4a8fd0"),
    _t("chart-2", "charts", _("Chart series 2"), "#b4532a", "#d06c3a"),
    _t("chart-grid", "charts", _("Chart grid lines"), "#e1dfd2", "#272d21"),
    _t("medal-bronze", "medals", _("Bronze medal"), "#a8642c", "#d99358"),
    _t("medal-silver", "medals", _("Silver medal"), "#7d8790", "#bcc4cb"),
    _t("medal-gold", "medals", _("Gold medal"), "#b07f06", "#e9bf45"),
    _t("medal-platinum", "medals", _("Platinum medal"), "#2f7f93", "#7fd0e2"),
)
TOKEN_KEYS: Final[frozenset[str]] = frozenset(token.key for token in TOKENS)
_BY_KEY: Final[dict[str, Token]] = {token.key: token for token in TOKENS}

# --- colour maths ----------------------------------------------------------------------------------------------


def _channels(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def luminance(color: str) -> float:
    """WCAG relative luminance of '#rrggbb'."""
    linear: list[float] = []
    for value in _channels(color):
        v = value / 255
        linear.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio, 1.0 to 21.0."""
    a, b = sorted((luminance(first), luminance(second)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def _mix(first: str, second: str, share: float) -> str:
    """`share` of `first` plus the rest of `second`, like CSS `color-mix(in srgb, ...)`."""
    one, two = _channels(first), _channels(second)
    return "#" + "".join(f"{round(a * share + b * (1 - share)):02x}" for a, b in zip(one, two, strict=True))


def auto_contrast(accent: str) -> str:
    """White or near-black text for a filled `accent` button, whichever reads better."""
    return "#ffffff" if contrast_ratio(accent, "#ffffff") >= contrast_ratio(accent, "#10161c") else "#10161c"


# --- the effective colours -------------------------------------------------------------------------------------


def clean_theme(raw: object) -> Theme:
    """The valid part of a stored/submitted theme: known tokens with '#RRGGBB' values, upper-cased. Never raises."""
    theme: Theme = {"light": {}, "dark": {}}
    if not isinstance(raw, Mapping):
        return theme
    for mode in MODES:
        values: object = raw.get(mode)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if not isinstance(values, Mapping):
            continue
        for key, value in values.items():  # pyright: ignore[reportUnknownVariableType]
            if key in TOKEN_KEYS and isinstance(value, str) and HEX_COLOR.fullmatch(value):
                theme[mode][str(key)] = value.upper()  # pyright: ignore[reportUnknownArgumentType]
    return theme


def effective(theme: Theme, mode: Mode) -> dict[str, str]:
    """Every token's colour as '#rrggbb' in `mode`: the override, else the default, else the derived value."""
    colors: dict[str, str] = {}
    chosen = theme[mode]
    for token in TOKENS:
        own = chosen.get(token.key)
        default = token.light if mode == "light" else token.dark
        if own is not None:
            colors[token.key] = own.lower()
        elif default is not None:
            colors[token.key] = default
    accent = colors["accent"]
    colors.setdefault("accent-contrast", auto_contrast(accent))
    colors.setdefault("link", _mix(accent, "#000000", 0.82) if mode == "light" else _mix(accent, "#ffffff", 0.52))
    return colors


# --- the <style> block -----------------------------------------------------------------------------------------

# key -> (label, font-family value). The '' entry is the shipped look: choosing it stores no override.
HEADING_FONTS: Final[dict[str, tuple[str, str]]] = {
    "": (
        _("Condensed (default)"),
        '"Barlow Condensed", "Arial Narrow", "Roboto Condensed", system-ui, sans-serif',
    ),
    "system": (
        _("System sans-serif"),
        'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    ),
    "humanist": (
        _("Humanist sans-serif"),
        'Seravek, "Gill Sans Nova", Ubuntu, Calibri, "DejaVu Sans", "Source Sans Pro", sans-serif',
    ),
    "serif": (_("Serif"), 'Charter, "Bitstream Charter", "Sitka Text", Cambria, Georgia, serif'),
    "slab": (_("Slab serif"), 'Rockwell, "Rockwell Nova", "Roboto Slab", "DejaVu Serif", "Sitka Small", serif'),
    "mono": (_("Monospace"), 'ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas, monospace'),
}
BODY_FONTS: Final[dict[str, tuple[str, str]]] = {
    "": (_("System sans-serif (default)"), ""),
    "humanist": HEADING_FONTS["humanist"],
    "serif": HEADING_FONTS["serif"],
    "mono": HEADING_FONTS["mono"],
}

_CSS_SAFE = re.compile(r"""[A-Za-z0-9#,.:;(){}\-\s"'%/@]*""")


def _declaration(token: Token, theme: Theme, colors: dict[Mode, dict[str, str]]) -> str | None:
    """`--il2-x: value;` when the theme overrides the token in either mode (or, for the accent's contrast colour, when
    the accent changed), else None."""
    key = token.key
    set_light, set_dark = key in theme["light"], key in theme["dark"]
    if key == "accent-contrast":
        set_light = set_light or "accent" in theme["light"]
        set_dark = set_dark or "accent" in theme["dark"]
    if not (set_light or set_dark):
        return None
    light, dark = colors["light"][key], colors["dark"][key]
    if not set_light:  # keep the stylesheet's own default for the mode that is not overridden
        light = _default_css(token, "light")
    if not set_dark:
        dark = _default_css(token, "dark")
    value = light if light == dark else f"light-dark({light}, {dark})"
    return f"{token.css_var}:{value};"


def _default_css(token: Token, mode: Mode) -> str:
    """The stylesheet's own value for the mode, written as CSS (the derived tokens refer to the accent)."""
    default = token.light if mode == "light" else token.dark
    if default is not None:
        return default
    if token.key == "link":
        return (
            "color-mix(in srgb, var(--il2-accent) 82%, black)"
            if mode == "light"
            else "color-mix(in srgb, var(--il2-accent) 52%, white)"
        )
    return "#ffffff"  # accent-contrast of the shipped accent


def _font_stack(
    key: str, uploads: dict[str, CustomFont], builtin: Mapping[str, tuple[StrOrPromise, str]], fallback: str
) -> str:
    """The `font-family` value for a choice: a built-in stack, or the uploaded font in front of `fallback`."""
    if key in uploads:
        return f'"{uploads[key].family}", {fallback}'
    return builtin[key][1] if key and key in builtin else ""


def theme_css(
    theme: object,
    heading_font: str = "",
    body_font: str = "",
    custom_fonts: object = (),
    media_url: str = "/media/",
) -> SafeString:
    """The `@font-face` rules and the `:root { ... }` rule for the page's <style> block; '' for the default look. Safe
    by construction. `custom_fonts` is the stored list of uploads; only those chosen as heading/body font are used."""
    cleaned = clean_theme(theme)
    colors: dict[Mode, dict[str, str]] = {"light": effective(cleaned, "light"), "dark": effective(cleaned, "dark")}
    parts = [d for token in TOKENS if (d := _declaration(token, cleaned, colors)) is not None]
    uploads = {font.key: font for font in clean_fonts(custom_fonts)}
    heading = _font_stack(heading_font, uploads, HEADING_FONTS, HEADING_FONTS[""][1])
    body = _font_stack(body_font, uploads, BODY_FONTS, HEADING_FONTS["system"][1])
    if heading:
        parts.append(f"--il2-font-display:{heading};")
    if body:
        parts.append(f"--pico-font-family:{body};")
    if not parts:
        return SafeString("")
    used = [font for key, font in uploads.items() if key in {heading_font, body_font}]
    css = font_face_css(used, media_url) + ":root{" + "".join(parts) + "}"
    if not _CSS_SAFE.fullmatch(css) or "<" in css or "\\" in css:  # defence in depth: cannot happen with the code above
        return SafeString("")
    return mark_safe(css)


# --- contrast warnings -----------------------------------------------------------------------------------------

# (foreground token, background token, minimum ratio, what it is). 4.5 is WCAG AA for normal text, 3.0 for large text
# and graphics.
CONTRAST_CHECKS: Final[tuple[tuple[str, str, float, StrOrPromise], ...]] = (
    ("text", "bg", 4.5, _("body text on the page background")),
    ("text", "surface", 4.5, _("body text on cards and tables")),
    ("heading", "bg", 4.5, _("headings on the page background")),
    ("muted", "surface", 4.5, _("secondary text on cards and tables")),
    ("muted", "bg", 4.0, _("secondary text on the page background")),
    ("link", "surface", 4.5, _("links on cards and tables")),
    ("link", "bg", 4.5, _("links on the page background")),
    ("accent-contrast", "accent", 4.5, _("text on accent buttons")),
    ("secondary-contrast", "secondary-bg", 4.5, _("text on secondary buttons")),
    ("band-text", "band", 4.5, _("header text")),
    ("band-muted", "band", 4.5, _("header menu text")),
    ("band-strong", "band", 4.5, _("header text on hover")),
    ("green", "surface", 4.5, _("green badges")),
    ("amber", "surface", 4.5, _("amber badges")),
    ("orange", "surface", 4.5, _("orange badges")),
    ("red", "surface", 4.5, _("red badges")),
    ("blue", "surface", 4.5, _("blue badges")),
    ("teal", "surface", 4.5, _("teal badges")),
    ("purple", "surface", 4.5, _("purple badges")),
    ("grey", "surface", 4.5, _("grey badges")),
    ("redfor", "surface", 4.5, _("REDFOR labels")),
    ("blufor", "surface", 4.5, _("BLUFOR labels")),
    ("chart-1", "surface", 3.0, _("chart series 1")),
    ("chart-2", "surface", 3.0, _("chart series 2")),
)


@dataclass(frozen=True, slots=True)
class ContrastWarning:
    mode: Mode
    foreground: str
    background: str
    what: StrOrPromise
    ratio: float
    minimum: float


def contrast_warnings(theme: object) -> list[ContrastWarning]:
    """Pairs whose effective colours fall below their minimum ratio, in either mode. Only pairs where the theme changed
    at least one of the two tokens are reported (the shipped look is not the admin's to fix)."""
    cleaned = clean_theme(theme)
    found: list[ContrastWarning] = []
    for mode in MODES:
        colors = effective(cleaned, mode)
        changed = set(cleaned[mode])
        if "accent" in changed:
            changed |= {"accent-contrast", "link"}
        for fg, bg, minimum, what in CONTRAST_CHECKS:
            if fg not in changed and bg not in changed:
                continue
            ratio = contrast_ratio(colors[fg], colors[bg])
            if ratio < minimum:
                found.append(ContrastWarning(mode, fg, bg, what, round(ratio, 1), minimum))
    return found


# --- presets ---------------------------------------------------------------------------------------------------


def _preset(light: str, dark: str) -> Theme:
    keys = [
        "bg",
        "surface",
        "surface-2",
        "surface-hover",
        "border",
        "border-strong",
        "text",
        "heading",
        "muted",
        "band",
        "band-text",
        "band-muted",
        "accent",
    ]
    return {
        "light": dict(zip(keys, light.split(), strict=True)),
        "dark": dict(zip(keys, dark.split(), strict=True)),
    }


PRESETS: Final[dict[str, tuple[str, Theme]]] = {
    "steel": (
        _("Steel blue"),
        _preset(
            "#E6EAF0 #F7F9FC #EDF1F6 #DFE6EF #CDD5E0 #AEBACB #1F2733 #0F1620 #566273 #1F2D40 #E8EEF7 #A9B8CC #2F6DB3",
            "#0F141B #151C26 #1B2430 #222E3C #28333F #3A4858 #D7DFEB #EEF3FA #8D9BB0 #0C1219 #E8EEF7 #9EB0C8 #4A8FD0",
        ),
    ),
    "desert": (
        _("Desert sand"),
        _preset(
            "#ECE4D3 #FAF6EC #F2EBDA #E9DFC8 #D9CDB2 #C0B08C #2E281C #1B160C #6D6450 #4A3B24 #F3EAD6 #CDBF9F #8A4B12",
            "#17130C #201A10 #281F13 #30261A #3A2F1D #51432B #E6DCC4 #F6EEDB #A39877 #120E07 #F3EAD6 #B5A784 #C9852F",
        ),
    ),
    "contrast": (
        _("High contrast (black and white)"),
        _preset(
            "#FFFFFF #FFFFFF #F2F2F2 #E6E6E6 #BDBDBD #6E6E6E #000000 #000000 #404040 #000000 #FFFFFF #D0D0D0 #0B3D91",
            "#000000 #0A0A0A #141414 #202020 #4A4A4A #8A8A8A #FFFFFF #FFFFFF #CFCFCF #000000 #FFFFFF #D0D0D0 #FFD24A",
        ),
    ),
}
